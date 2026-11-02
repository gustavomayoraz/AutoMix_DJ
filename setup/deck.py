"""AutoMix — un deck (reproductor con su onda, tempo y rejilla)."""
from pathlib import Path
import math
import os
import threading
import time
import numpy as np
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout
from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from setup import cruce_auto, nivel
from setup.ajustes import NIVEL_V2, SUPPORTED, TARGET_LUFS_RMS, fmt_time
from setup.analisis import (_guardar_cache, _limpiar_grilla_cache, _mantener_vivo, aplicar_uno, desfase_rejilla_auto,
                            desfase_rejilla_bombos, desplazar_rejilla, grilla_manual, load_audio_stereo, plegar_bpm)
from setup import almacen
from setup.base import CACHE_DIR, _log_diferido, log
from setup.hilos_analisis import Analyzer, RehacerUno
from setup.tema import TEMA, _rgba
from setup.widgets import EtiquetaTema, RelojReset
from setup.wsola import WsolaFlujo


class Deck(QFrame):
    audio_fallo = Signal(str)          # el audio de este tema no se pudo cargar (archivo dañado): la ventana lo anota para saltearlo

    def __init__(self, name, color, on_analysis_cb):
        super().__init__()
        self.name = name
        self.on_analysis_cb = on_analysis_cb
        self.path = None
        self.analysis = None
        self.audio = None
        self.sr = 44100
        self.cursor = 0
        self._cursor_frac = 0.0
        self.playing = False
        self.vol_hi = 1.0; self.vol_lo = 1.0      # ganancia de medios+agudos y de graves (la aplica el mezclador, por bandas)
        self._db_cache = None
        self.volume = 1.0          # volumen de cada deck fijo al 100 %: el nivel se maneja con el crossfader y el volumen general
        self.auto_gain = 1.0
        self.tempo_rate = 1.0
        self.pos_manual = False     # el usuario ubicó el tema con la manito: la mezcla automática lo respeta
        self.tempo_manual = False   # el usuario fijó el tempo a mano: nada lo toca hasta que mezcle o apriete Reset
        self.trim = 1.0
        self.on_play_cb = None
        self.on_reset_cb = None
        self.lock = threading.Lock()
        self.original_bpm = None
        self._ws = WsolaFlujo(); self._ws_activo = False; self._ws_exp = None      # estirado de tiempo sin cambiar el tono
        self.pitch = 1.0; self.semitonos = 0                                       # cambio de tonalidad del tema (ver set_semitonos): 1,0 = el tono original
        self._pt_ok = False; self._pt_buf = None; self._pt_ph = 0.0; self._pt_pos = 0.0; self._pt_fi = 0
        self._ws_modo = False
        self.loop = None               # (inicio, fin) en muestras del tema mientras hay un loop activo
        self.loop_desfase = 0.0        # muestras que el loop "retrocedió" en total: posición virtual = cursor + desfase
        self._loop_salir = False       # pedido (desde la interfaz) de soltar el loop
        self._fi_pend = False          # el próximo bloque empieza con un fundido de entrada (después de un salto)
        self._fi_pos = 0               # cuánto del fundido de entrada ya se aplicó
        self._ws_fi_pos = 128          # ídem, para la entrada suave del estirado (128 = terminado)
        self._auto_centrado_sesion = False   # ya se intentó el auto-centrado para la carga actual

        self.setAcceptDrops(True)
        self._acento = color
        self.setProperty("cargado", False)
        self.setStyleSheet(self._qss_deck(color))
        efecto = QGraphicsDropShadowEffect(self)
        efecto.setBlurRadius(34); efecto.setOffset(0, 3); efecto.setColor(QColor(_rgba(color, 120)))
        self.setGraphicsEffect(efecto)
        self._efecto = efecto
        self._glow_paso = -1
        self.setMinimumHeight(124); self.setMaximumHeight(16777215)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)      # el deck se estira hasta cubrir el alto de la columna

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(2)

        top = QHBoxLayout(); top.setSpacing(8); top.setContentsMargins(0, 0, 0, 0)     # arriba: título / BPM a la izquierda y el ▶ GRANDE a la derecha
        col_t = QVBoxLayout(); col_t.setSpacing(3); col_t.setContentsMargins(0, 0, 0, 0)
        r1 = QHBoxLayout()
        self.lbl_title = QLabel(f"DECK {name}")
        self.lbl_title.setStyleSheet(f"color:{color}; font-size:16px; font-weight:bold;")
        self.lbl_title.setFixedWidth(112)
        r1.addWidget(self.lbl_title)
        self.lbl_track = EtiquetaTema("Arrastrá una canción aquí")
        self.on_ubicar_cb = None
        self.lbl_track.clicked.connect(lambda: self.on_ubicar_cb(self.path) if self.on_ubicar_cb else None)
        self.lbl_track.setStyleSheet(f"color:{TEMA['texto']}; font-size:11px;")
        r1.addWidget(self.lbl_track, stretch=1)
        col_t.addLayout(r1)

        r2 = QHBoxLayout()
        self.lbl_bpm = QLabel("BPM: --")
        self.lbl_bpm.setStyleSheet(f"color:{TEMA['ambar']}; font-size:11px; font-weight:bold;")
        self.lbl_bpm.setFixedWidth(220)
        r2.addWidget(self.lbl_bpm)
        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setStyleSheet("color:#ffffff; font-size:12px; font-weight:bold;")
        r2.addWidget(self.lbl_time, stretch=1)
        col_t.addLayout(r2)
        col_t.addStretch(1)
        top.addLayout(col_t, 1)
        lay.addLayout(top, 3)

        r3 = QHBoxLayout()
        r3.setSpacing(4)
        self.btn_play = QPushButton("▶")
        self.btn_pause = QPushButton("⏸")
        self.btn_stop = QPushButton("⏹")
        self.btn_reset = QPushButton("⟲")
        for b in (self.btn_pause, self.btn_stop, self.btn_reset):       # (el ▶ pasó arriba, grande: los demás se reparten su lugar)
            b.setMinimumHeight(30); b.setMaximumHeight(48); b.setFixedWidth(60)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding); r3.addWidget(b)
            fb = b.font(); fb.setPointSizeF(14.0); fb.setBold(True); b.setFont(fb)
        self.btn_play.setMinimumHeight(48); self.btn_play.setMaximumHeight(16777215); self.btn_play.setMinimumWidth(150)
        self.btn_play.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        fp = self.btn_play.font(); fp.setPointSizeF(28.0); fp.setBold(True); self.btn_play.setFont(fp)
        self.btn_play.setToolTip("Reproducir")
        top.addWidget(self.btn_play, 2)
        self.btn_play.setProperty("rol", "play"); self.btn_pause.setProperty("rol", "pausa"); self.btn_stop.setProperty("rol", "stop")
        self.btn_play.clicked.connect(self.play)
        self.btn_pause.clicked.connect(self.pause)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_reset.clicked.connect(lambda *_: self.reset_tempo(suave=True))
        self.btn_reset.setToolTip("Resetear el tempo al 100 % (el tema sigue donde está)")
        r3.addSpacing(6)
        r3.addWidget(QLabel("Tempo"))                    # el slider de tempo va en la misma fila, a la derecha del botón de reset
        self.sld_tempo = QSlider(Qt.Orientation.Horizontal)
        self.sld_tempo.setRange(50, 200); self.sld_tempo.setValue(100)
        self.sld_tempo.setMinimumWidth(70)
        r3.addWidget(self.sld_tempo, stretch=1)
        self._flechas_tempo = []
        self.reloj_reset = RelojReset(self, color)
        for txt, paso, tip in (("◀", -1, "Tempo −1 %"), ("▶", 1, "Tempo +1 %")):
            b = QPushButton(txt); self._flechas_tempo.append(b); b.setFixedSize(24, 24); b.setAutoRepeat(True); b.setAutoRepeatDelay(350); b.setAutoRepeatInterval(90)
            b.setToolTip(tip + "  (el tempo se mantiene hasta que mezcle o toques Reset)")
            b.setStyleSheet("QPushButton { padding:0px; font-size:11px; }")
            b.clicked.connect(lambda _=False, d=paso: self.sld_tempo.setValue(self.sld_tempo.value() + d))
            r3.addWidget(b)
        lay.addLayout(r3, 2)

        self.sld_tempo.valueChanged.connect(self._update_tempo)

    @staticmethod
    def _qss_deck(color):
        c = QColor(color); h = max(0, c.hue())
        f1 = QColor.fromHsv(h, 210, 104).name(); f2 = QColor.fromHsv(h, 190, 48).name()
        f3 = QColor.fromHsv(h, 165, 19).name()
        osc = QColor.fromHsv(h, 230, 150).name()
        return (
            f"Deck {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {f1}, stop:0.55 {f2}, stop:1 {f3});"
            f" border:1px dashed {_rgba(color, 150)}; border-radius:14px; }}"
            f"Deck[cargado=\"true\"] {{ border:1px solid {_rgba(color, 230)}; }}"
            f"Deck QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {osc}, stop:1 {color}); border-radius:4px; }}"
            f"Deck QSlider::handle:horizontal {{ border:2px solid {color}; }}"
        )

    def set_brillo(self, peso):
        """Resplandor del deck según cuánto está sonando (0..1): se enciende el que entra y se apaga el que sale."""
        peso = max(0.0, min(1.0, float(peso)))
        c = QColor(self._acento)
        self._efecto.setColor(QColor(c.red(), c.green(), c.blue(), int(45 + 150 * peso)))
        self._efecto.setBlurRadius(18 + 26 * peso)

    def _marcar_cargado(self, v):
        self.setProperty("cargado", bool(v))
        self.style().unpolish(self); self.style().polish(self)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isfile(p) and p.lower().endswith(SUPPORTED):
                self.load_track(p)
                break

    def load_dialog(self):
        f, _ = QFileDialog.getOpenFileName(
            self, f"Cargar en Deck {self.name}", "", "Audio (*.mp3 *.wav *.flac *.ogg *.m4a)"
        )
        if f:
            self.load_track(f)

    def descargar(self):
        """Deck vacío: como recién abierto el programa (sin tema, sin análisis, tempo al 100 %)."""
        self._cancelar_reset_suave()
        self.stop()
        with self.lock:
            self.audio = None
            self.cursor = 0
            self._cursor_frac = 0.0
        self.analysis = None
        self.original_bpm = None
        self.path = None
        self.auto_gain = 1.0
        self._ag_listo = False
        self._db_cache = None
        self.set_semitonos(0)
        self._auto_centrado_sesion = False
        self.tempo_rate = 1.0
        self.tempo_manual = False
        self.pos_manual = False
        self.trim = 1.0
        if self.sld_tempo.value() != 100:
            self.sld_tempo.blockSignals(True); self.sld_tempo.setValue(100); self.sld_tempo.blockSignals(False)
        self._marcar_cargado(False)
        self.lbl_track.setText("Arrastrá una canción aquí"); self.lbl_track.setToolTip("")
        self.lbl_bpm.setText("BPM: --")
        self.lbl_time.setText("00:00 / 00:00")
        log(f"deck {self.name}", "vaciado")

    def load_track(self, path):
        self.stop()
        self.pos_manual = False
        self._auto_centrado_sesion = False        # nuevo tema → nueva oportunidad de auto-centrar
        with self.lock:
            self.audio = None
            self.cursor = 0
            self._cursor_frac = 0.0
            self._ws_activo = False
        self.set_semitonos(0)
        self.analysis = None
        self.original_bpm = None
        self.auto_gain = 1.0
        self._ag_listo = False
        self.path = path
        log(f"deck {self.name}", f"cargando: {os.path.basename(path)}")
        self._marcar_cargado(True)
        self.lbl_track.setText(os.path.basename(path))
        self.lbl_bpm.setText("⏳ Analizando…")
        self.analyzer = Analyzer(path)
        _mantener_vivo(self.analyzer)
        self.analyzer.done.connect(self._on_analysis)
        self.analyzer.uno_listo.connect(self._uno_de_cache)
        self.analyzer.start()
        threading.Thread(target=self._load_audio, args=(path,), daemon=True).start()

    def _load_audio(self, path):
        try:
            y, sr = load_audio_stereo(path, sr=44100)
            # TODO lo pesado (copia contigua, Auto-Gain) FUERA del lock: el lock del deck lo toma
            # también el callback de audio, y si se queda esperando se oye un corte en lo que suena.
            y32 = np.ascontiguousarray(y, dtype=np.float32)
            y32, recorte = self._recortar_silencio_final(y32, sr)
            ag = self._calc_auto_gain(y32, sr)
            with self.lock:
                if path != self.path:
                    return
                self.auto_gain = ag
                self._ag_listo = True
                self.audio = y32
                self.sr = sr
                self.cursor = 0
                self._cursor_frac = 0.0
                if self.analysis:                                  # el análisis llegó antes que el audio
                    self.analysis["duration"] = min(float(self.analysis.get("duration") or 0) or len(y32) / sr, len(y32) / sr) if recorte else self.analysis.get("duration")
            if recorte:
                log(f"deck {self.name}", f"silencio final recortado: {recorte:.1f} s")
            log(f"deck {self.name}", f"audio listo: {len(y32) / sr:.1f} s · {sr} Hz · auto-gain x{ag:.2f} · {os.path.basename(path)}")
        except Exception as e:
            print("Error cargando audio:", e)
            if path == self.path:
                self.audio_fallo.emit(path)

    SILENCIO_FIN_DB = -50.0      # por debajo de esto (RMS en ventanas de 50 ms) se considera silencio
    SILENCIO_FIN_MIN_S = 1.0     # solo se recorta si el silencio final dura al menos esto
    SILENCIO_FIN_COLA_S = 0.25   # se deja esta cola después del último sonido (que no suene cortado)

    @classmethod
    def _recortar_silencio_final(cls, audio, sr):
        """Quita el silencio del final del tema. Devuelve (audio, segundos_recortados). Solo mira el audio ya cargado:
        funciona igual con temas en caché y no toca el archivo original."""
        try:
            v = max(256, int(sr * 0.05))
            n = len(audio) // v
            if n < 40:
                return audio, 0.0
            bl = audio[:n * v].reshape(n, v, -1)
            rms = np.sqrt(np.einsum("bij,bij->b", bl, bl, dtype=np.float64) / (v * bl.shape[2]))
            vivo = np.nonzero(rms > 10.0 ** (cls.SILENCIO_FIN_DB / 20.0))[0]
            if len(vivo) == 0:
                return audio, 0.0
            fin = int((vivo[-1] + 1) * v + cls.SILENCIO_FIN_COLA_S * sr)
            fin = max(fin, int(5.0 * sr))
            sobra = (len(audio) - fin) / float(sr)
            if sobra < cls.SILENCIO_FIN_MIN_S or fin >= len(audio):
                return audio, 0.0
            out = np.ascontiguousarray(audio[:fin])
            k = min(len(out), int(0.03 * sr))                      # fundido de 30 ms: sin clic en el corte
            if k > 1:
                out[-k:] *= np.linspace(1.0, 0.0, k, dtype=np.float32)[:, None]
            return out, sobra
        except Exception:
            return audio, 0.0

    @staticmethod
    def _calc_auto_gain(audio, sr=44100):
        """Auto-Gain a partir del audio ya cargado (sin librosa ni re-análisis). Con NIVEL_LUFS (⚙ Ajustes) mide la sonoridad real
        (filtro K y compuertas: ignora silencios e intros calladas); si no se puede, usa el RMS de siempre."""
        try:
            if cruce_auto.NIVEL_LUFS >= 0.5:
                g = nivel.ganancia_k(audio, sr, TARGET_LUFS_RMS)
                if g is not None:
                    return g
        except Exception:
            import traceback; traceback.print_exc()
        try:
            n = (len(audio) // 1024) * 1024
            if n < 2048:
                return 1.0
            bloques = audio[:n].reshape(-1, 1024, 2)
            e = np.einsum("bij,bij->b", bloques, bloques, dtype=np.float64) / (1024 * 2)
            rms_medio = float(np.mean(np.sqrt(e)))
            return float(np.clip(TARGET_LUFS_RMS / rms_medio, 0.25, 4.0)) if rms_medio > 1e-6 else 1.0
        except Exception:
            return 1.0

    @staticmethod
    def _afinar_bpm(res):
        """El BPM global de librosa suele errar 1-2 %. Con la grilla de beats detectada se calcula uno
        mucho más preciso (media de los intervalos sin los atípicos)."""
        try:
            b = np.asarray(res.get("beats_full") or [], dtype=np.float64)
            if len(b) < 16:
                return
            iv = np.diff(b)
            m = float(np.median(iv))
            k = iv[np.abs(iv - m) < 0.08 * m]
            if len(k) < 12 or m <= 0:
                return
            fino = 60.0 / float(np.mean(k))
            if abs(fino - res["bpm"]) / res["bpm"] < 0.03:      # solo afina; no cambia de "mitad/doble"
                res["bpm_librosa"] = res["bpm"]
                res["bpm"] = float(fino)
        except Exception:
            pass

    def _on_analysis(self, path, res):
        if path != self.path:
            return
        if "error" in res:
            self.lbl_bpm.setText("Error")
            return
        _limpiar_grilla_cache(res)
        self._afinar_bpm(res)
        if plegar_bpm(res):                                   # BPM absurdo (p. ej. 1535): se lleva al rango musical y se guarda
            log(f"deck {self.name}", f"BPM fuera de rango corregido → {res['bpm']:.1f}")
            try:
                _guardar_cache(almacen.ruta_cache(self.path), res)
            except Exception:
                pass
        self.analysis = res
        if self.audio is not None and self.sr:                 # audio ya recortado: la duración útil manda
            d_audio = len(self.audio) / float(self.sr)
            if 0 < d_audio < float(res.get("duration") or 0) - 0.5:
                res["duration"] = d_audio
        self.original_bpm = res["bpm"]
        if not getattr(self, "_ag_listo", False):
            self.auto_gain = res.get("auto_gain", 1.0)
        self._update_bpm_label()
        self.lbl_time.setText(f"00:00 / {fmt_time(res['duration'])}")
        if self.on_analysis_cb:
            self.on_analysis_cb(self.name, res)
        # Auto-centrado de la grilla: se hace acá, deferido, para no tocar nada mientras la UI se está armando
        QTimer.singleShot(200, self._auto_centrar)

    def _auto_centrar(self):
        """Auto-centra la grilla en los bombos al cargar un tema (sin que el usuario tenga que hacer nada).
        Mide con `desfase_rejilla_auto` (bombos → fallback RMS) y aplica el corrimiento si es razonable.
        La grilla ya no se corrige a mano: el cálculo automático es la única fuente."""
        if not self.analysis or not self.path:
            return
        if getattr(self, "_auto_centrado_sesion", False):
            return
        self._auto_centrado_sesion = True
        self.analysis.pop("usuario", None)               # restos de correcciones a mano de versiones anteriores
        try:
            off = desfase_rejilla_auto(self.analysis)
        except Exception:
            import traceback; traceback.print_exc()
            return
        if off is None:
            log(f"deck {self.name}", "grilla auto: no se pudo medir (bombos y energía RMS poco claros), queda como está")
            return
        if abs(off) < 0.008:
            log(f"deck {self.name}", "grilla auto: ya estaba centrada (±8 ms)")
            return
        if abs(off) > 0.15:
            log(f"deck {self.name}", f"grilla auto: corrimiento {off*1000:+.0f} ms sospechoso (¿BPM doble/mitad?), no se aplica")
            return
        try:
            with self.lock:
                desplazar_rejilla(self.analysis, off)
                self._db_cache = None
            _guardar_cache(almacen.ruta_cache(self.path), self.analysis)
            log(f"deck {self.name}", f"grilla auto: corrida {off*1000:+.0f} ms sobre los bombos")
            if self.on_analysis_cb:
                self.on_analysis_cb(self.name, self.analysis)
        except Exception:
            import traceback; traceback.print_exc()

    def centrar_rejilla(self, avisar=True):
        """Mueve la grilla para que los tiempos caigan sobre los bombos (o RMS si no hay kick).
        Devuelve el corrimiento en ms (None = no se pudo medir, 0.0 = ya estaba centrada)."""
        if not self.analysis or not self.path:
            return None
        off = desfase_rejilla_auto(self.analysis)
        if off is None:
            return None
        if abs(off) < 0.008:                              # ya está bien centrada (menos de 8 ms)
            return 0.0
        with self.lock:
            desplazar_rejilla(self.analysis, off)
            self._db_cache = None
        try:
            _guardar_cache(almacen.ruta_cache(self.path), self.analysis)
        except Exception:
            import traceback; traceback.print_exc()
        if avisar and self.on_analysis_cb:
            self.on_analysis_cb(self.name, self.analysis)
        return off * 1000.0

    def corregir_rejilla(self, factor=1.0, uno_en=None, usuario=False, avisar=True):
        """Cambia la grilla del tema cargado (la usa el modo Auto: octava ×2 / ÷2), la guarda en su caché y avisa a la ventana.
        Si cambió la octava, el "1" del compás se vuelve a buscar solo."""
        if not self.analysis or not self.path:
            return False
        try:
            with self.lock:
                try:
                    grilla_manual(self.analysis, factor, uno_en)
                except ValueError as e:
                    self.motivo_rechazo = str(e)
                    return False
                if usuario:
                    self.analysis["usuario"] = True          # la corrigió el usuario: el modo Auto ya no la toca
                self.original_bpm = self.analysis["bpm"]
                self._db_cache = None
            self._update_bpm_label()
            try:
                _guardar_cache(almacen.ruta_cache(self.path), self.analysis)
            except Exception:
                import traceback; traceback.print_exc()
            if avisar and self.on_analysis_cb:
                self.on_analysis_cb(self.name, self.analysis)
            if factor and factor != 1.0:
                self._rehacer_uno()
            return True
        except Exception:
            import traceback; traceback.print_exc()
            return False

    def _rehacer_uno(self):
        """La grilla cambió de octava: el compás tiene otro largo, así que el "1" se busca de nuevo (en segundo plano)."""
        if not self.analysis or not self.path:
            return
        self.analysis.pop("uno_auto", None)
        h = RehacerUno(self.path, self.analysis.get("beats_full") or [])
        h.listo.connect(self._uno_rehecho)
        _mantener_vivo(h)
        h.start()

    def _uno_de_cache(self, path, res):
        """Un análisis guardado antes del "1" automático: llega el "1" (y los bombos) calculado en segundo plano."""
        if path != self.path or not self.analysis or not res:
            return
        if res.get("kicks") and not self.analysis.get("kicks_full"):
            self.analysis["kicks_full"] = res["kicks"]
            self.analysis["kicks"] = res["kicks"][::len(res["kicks"]) // 400 + 1]
        r = res.get("uno")
        if r and not self.playing:                       # sonando no se toca: se oiría un salto
            with self.lock:
                aplicar_uno(self.analysis, r[0], r[1])
                self._db_cache = None
            log(f"deck {self.name}", f"el 1 calculado en segundo plano · confianza {r[1]:.2f}")
            if self.on_analysis_cb:
                self.on_analysis_cb(self.name, self.analysis)
        try:
            _guardar_cache(almacen.ruta_cache(self.path), self.analysis)
        except Exception:
            import traceback; traceback.print_exc()

    def _uno_rehecho(self, path, r):
        if path != self.path or not self.analysis or not r or self.playing:   # sonando no se toca: se oiría un salto
            return
        with self.lock:
            aplicar_uno(self.analysis, r[0], r[1])
            self._db_cache = None
        try:
            _guardar_cache(almacen.ruta_cache(self.path), self.analysis)
        except Exception:
            import traceback; traceback.print_exc()
        log(f"deck {self.name}", f"el 1 recalculado tras cambiar la octava · confianza {r[1]:.2f}")
        if self.on_analysis_cb:
            self.on_analysis_cb(self.name, self.analysis)

    def _update_bpm_label(self):
        if self.original_bpm is None:
            return
        eff = self.original_bpm * self.tempo_rate
        dif = (self.tempo_rate - 1.0) * 100.0                 # cuánto se movió el tempo respecto del tema original
        # BPM REAL del tema (el mismo de la lista) ▸ BPM con el que suena ahora (diferencia en %)
        if abs(dif) < 0.05:
            self.lbl_bpm.setText(f"BPM: {self.original_bpm:.1f} (sin ajuste)")
        else:
            self.lbl_bpm.setText(f"BPM: {self.original_bpm:.1f} ▸ {eff:.2f} ({dif:+.1f}%)")
        self.lbl_bpm.setToolTip(f"BPM real del tema: {self.original_bpm:.2f}\n"
                                f"Suena a: {eff:.2f} BPM ({dif:+.2f} % de tempo, para emparejarlo con el otro deck)\n"
                                f"AutoGain (parejar el volumen del tema): {self.auto_gain:.2f}x")

    @property
    def rate_eff(self):
        return self.tempo_rate * self.trim

    def play(self):
        if self.audio is None:
            return
        if self.on_play_cb is not None and not self.playing:
            try:
                self.on_play_cb(self)
            except Exception:
                import traceback; traceback.print_exc()
        if not self.playing:
            log(f"deck {self.name}", f"▶ play · {self.lbl_bpm.text().replace(chr(10), ' ')} · tempo {self.tempo_rate * 100:.0f} %")
        self.playing = True

    def pause(self):
        if self.playing:
            log(f"deck {self.name}", f"⏸ pausa en {self.position_seconds():.1f} s")
        self.playing = False

    def stop(self):
        if self.playing:
            log(f"deck {self.name}", "⏹ stop")
        self.playing = False
        self.trim = 1.0
        self.set_semitonos(0)
        with self.lock:
            self.cursor = 0
            self._cursor_frac = 0.0
            self._ws_activo = False
            self.loop = None; self.loop_desfase = 0.0; self._loop_salir = False

    RESET_SUAVE_S = 3.0

    def _ubicar_reloj(self):
        """Lo deja en la misma columna que las dos flechitas, pero subido hasta el centro del botón ▶ (bien visible sobre el verde)."""
        a, b = self._flechas_tempo
        c = a.mapTo(self, QPoint(0, 0)); d = b.mapTo(self, QPoint(b.width(), 0))
        pc = self.btn_play.mapTo(self, QPoint(0, self.btn_play.height() // 2))          # centro vertical del botón ▶ (la zona verde)
        self.reloj_reset.move((c.x() + d.x() - self.reloj_reset.width()) // 2, pc.y() - self.reloj_reset.height() // 2)
        self.reloj_reset.raise_()

    def _paso_reset_suave(self):
        k = min(1.0, (time.monotonic() - self._rs_t0) / self.RESET_SUAVE_S)
        self.reloj_reset.set_progreso(k)
        e = 0.5 - 0.5 * math.cos(k * math.pi)
        self.tempo_rate = self._rs_r0 + (1.0 - self._rs_r0) * e
        pct = int(round(self.tempo_rate * 100))
        if self.sld_tempo.value() != pct:
            self.sld_tempo.blockSignals(True); self.sld_tempo.setValue(pct); self.sld_tempo.blockSignals(False)
        self._update_bpm_label()
        if k >= 1.0:
            self._rs_timer.stop(); self.tempo_rate = 1.0
            self._update_bpm_label()
            QTimer.singleShot(350, lambda: None if self._rs_timer.isActive() else self.reloj_reset.hide())

    def _cancelar_reset_suave(self):
        t = getattr(self, "_rs_timer", None)
        if t is not None and t.isActive():
            t.stop()
        t = getattr(self, "_aj_timer", None)
        if t is not None and t.isActive():
            t.stop()
        self._aj_gen = getattr(self, "_aj_gen", 0) + 1
        self.reloj_reset.hide()

    def _reset_activo(self):
        t = getattr(self, "_rs_timer", None)
        return t is not None and t.isActive()

    def mostrar_ajuste(self, k):
        if self._reset_activo():
            return
        self._aj_gen = getattr(self, "_aj_gen", 0) + 1
        self.reloj_reset.set_progreso(k)
        if not self.reloj_reset.isVisible():
            self._ubicar_reloj(); self.reloj_reset.show()

    def ocultar_ajuste(self, demora=350):
        self._aj_gen = getattr(self, "_aj_gen", 0) + 1
        g = self._aj_gen
        def _h():
            if g == getattr(self, "_aj_gen", 0) and not self._reset_activo():
                self.reloj_reset.hide()
        QTimer.singleShot(demora, _h)

    def barrido_ajuste(self, dur=1.3):
        if self._reset_activo():
            return
        if getattr(self, "_aj_timer", None) is None:
            self._aj_timer = QTimer(self); self._aj_timer.setInterval(30)
            self._aj_timer.timeout.connect(self._paso_barrido)
        self._aj_t0 = time.monotonic(); self._aj_dur = float(dur)
        self.mostrar_ajuste(0.0)
        self._aj_timer.start()

    def _paso_barrido(self):
        k = min(1.0, (time.monotonic() - self._aj_t0) / max(0.05, self._aj_dur))
        self.mostrar_ajuste(0.5 - 0.5 * math.cos(k * math.pi))
        if k >= 1.0:
            self._aj_timer.stop()
            self.ocultar_ajuste()

    def reset_tempo(self, suave=False):
        self.trim = 1.0
        self.tempo_manual = False
        self._cancelar_reset_suave()
        if abs(self.tempo_rate - 1.0) > 1e-6:
            log(f"deck {self.name}", f"tempo {self.tempo_rate * 100:.0f} % → 100 % ({'progresivo' if suave and self.playing else 'inmediato'})")
        if suave and self.playing and abs(self.tempo_rate - 1.0) > 0.004:
            if getattr(self, "_rs_timer", None) is None:
                self._rs_timer = QTimer(self); self._rs_timer.setInterval(30)
                self._rs_timer.timeout.connect(self._paso_reset_suave)
            self._rs_r0 = self.tempo_rate; self._rs_t0 = time.monotonic()
            self.reloj_reset.set_progreso(0.0); self._ubicar_reloj(); self.reloj_reset.show()
            self._rs_timer.start()
        elif abs(self.tempo_rate - 1.0) > 1e-6:
            self.tempo_rate = 1.0
            pct = 100
            if self.sld_tempo.value() != pct:
                self.sld_tempo.blockSignals(True)
                self.sld_tempo.setValue(pct)
                self.sld_tempo.blockSignals(False)
        self._update_bpm_label()
        if self.on_reset_cb is not None:
            try:
                self.on_reset_cb(self)
            except Exception:
                import traceback; traceback.print_exc()

    reset_time = reset_tempo      # nombre anterior, por compatibilidad

    def _update_tempo(self):
        self._cancelar_reset_suave()
        self.tempo_rate = self.sld_tempo.value() / 100.0
        self.tempo_manual = (self.sld_tempo.value() != 100)
        _log_diferido(f"tempo{self.name}", f"deck {self.name}", f"tempo manual {self.sld_tempo.value()} % ({(self.original_bpm or 0) * self.tempo_rate:.1f} BPM)")
        self._update_bpm_label()

    def set_tempo_from_fader(self, rate):
        rate = float(np.clip(rate, 0.5, 2.0))
        self.tempo_rate = rate
        pct = int(round(rate * 100))
        if self.sld_tempo.value() != pct:
            self.sld_tempo.blockSignals(True)
            self.sld_tempo.setValue(pct)
            self.sld_tempo.blockSignals(False)
        self._update_bpm_label()

    def position_seconds(self):
        with self.lock:
            return self.cursor / self.sr if self.audio is not None else 0.0

    def position_virtual_seconds(self):
        """Posición como si el tema NO estuviera en loop (la que usan la sincronía y la mezcla automática)."""
        with self.lock:
            return (self.cursor + self._cursor_frac + self.loop_desfase) / self.sr if self.audio is not None else 0.0

    def set_position_seconds(self, t):
        if self.audio is None:
            return
        t = float(np.clip(t, 0.0, len(self.audio) / self.sr - 0.001))
        with self.lock:
            self.cursor = int(t * self.sr)
            self._cursor_frac = 0.0
            self.loop = None; self.loop_desfase = 0.0; self._loop_salir = False

    def tick(self):
        if self.analysis and self.audio is not None:
            pos = self.position_seconds()
            self.lbl_time.setText(f"{fmt_time(pos)} / {fmt_time(self.analysis['duration'])}")

    WSOLA = True

    SINC_T = 32
    SINC_FASES = 512
    _SINC_K = np.arange(32) - 15
    _SINC_TAB = None

    @staticmethod
    def _tabla_sinc(fc=0.96, beta=9.0):
        if Deck._SINC_TAB is None:
            T, F = Deck.SINC_T, Deck.SINC_FASES
            ph = np.arange(F + 1) / F
            d = Deck._SINC_K[None, :] - ph[:, None]
            h = fc * np.sinc(fc * d) * np.i0(beta * np.sqrt(np.clip(1 - (d / (T / 2)) ** 2, 0, 1))) / np.i0(beta)
            h /= h.sum(1, keepdims=True)
            Deck._SINC_TAB = h.astype(np.float32)
        return Deck._SINC_TAB

    def _render_directo(self, frames, rate):
        """Lectura sin estirado: exacta a velocidad 1,0 y, si no, por remuestreo sinc de alta calidad (cambia el tono)."""
        n_audio = len(self.audio)
        if abs(rate - 1.0) < 1e-9 and self._cursor_frac == 0.0:
            chunk = self.audio[self.cursor:self.cursor + frames]
            if len(chunk) < frames:
                pad = np.zeros((frames - len(chunk), 2), dtype=np.float32)
                chunk = np.vstack([chunk, pad])
                self.playing = False
            self.cursor += frames
            return chunk
        pos = self.cursor + self._cursor_frac + np.arange(frames, dtype=np.float64) * rate
        i1 = np.floor(pos).astype(np.int64)
        if i1[-1] + 2 >= n_audio:
            self.playing = (i1[0] + 2 < n_audio)
        # Interpolador sinc con ventana Kaiser (32 puntos): conserva los agudos hasta ~20 kHz (el cúbico los degradaba)
        tab = Deck._tabla_sinc()
        fr = (pos - i1) * Deck.SINC_FASES
        j = np.floor(fr).astype(np.int64)
        w = (fr - j).astype(np.float32)[:, None]
        H = tab[j] * (1.0 - w) + tab[j + 1] * w
        idx = np.clip(i1[:, None] + Deck._SINC_K[None, :], 0, n_audio - 1)
        y = np.einsum("nt,ntc->nc", H, self.audio[idx])
        valid = (pos < n_audio - 1)[:, None]
        total = self._cursor_frac + frames * rate
        int_adv = int(total)
        self._cursor_frac = total - int_adv
        self.cursor += int_adv
        return (y * valid).astype(np.float32)

    WS_ENTRA = 0.020
    WS_SALE = 0.012

    def _ws_usar(self, rate):
        d = abs(rate - 1.0)
        if d <= 1e-9:
            self._ws_modo = False
        elif self._ws_modo:
            self._ws_modo = d >= self.WS_SALE
        else:
            self._ws_modo = d > self.WS_ENTRA
        return self._ws_modo

    def _render_wsola(self, frames, rate):
        salto = False
        if not self._ws_activo or self._ws_exp != (self.cursor, self._cursor_frac):
            salto = self._ws_activo
            self._ws.reset(self.cursor + self._cursor_frac)
            self._ws_activo = True
        y, fin = self._ws.leer(self.audio, frames, float(np.clip(rate, 0.5, 2.0)))
        if salto:
            self._ws_fi_pos = 0
        if self._ws_fi_pos < 128:
            k = min(128 - self._ws_fi_pos, frames)
            y[:k] *= ((np.arange(k, dtype=np.float32) + self._ws_fi_pos + 1.0) / 128.0)[:, None]
            self._ws_fi_pos += k
        h = self._ws.head
        self.cursor = int(h)
        self._cursor_frac = h - self.cursor
        self._ws_exp = (self.cursor, self._cursor_frac)
        if fin or self.cursor >= len(self.audio) - 2:
            self.playing = False
        return y

    def _render_bloque(self, frames):
        out = np.zeros((frames, 2), dtype=np.float32)
        with self.lock:
            if not self.playing or self.audio is None:
                return out
            rate = self.rate_eff
            ganancia_total = self.auto_gain * NIVEL_V2
            if self.pitch != 1.0:
                try:
                    y = self._render_tono(frames, rate)
                except Exception:
                    import traceback; traceback.print_exc()
                    self.pitch = 1.0; self.semitonos = 0; self._pt_ok = False; self._ws_activo = False
                    print("[audio] cambio de tonalidad desactivado por un error")
                    y = self._render_normal(frames, rate)
            else:
                if self._pt_ok:                                   # salió del modo tono: el estirado normal arranca limpio desde el cursor
                    self._pt_ok = False; self._ws_activo = False
                y = self._render_normal(frames, rate)
            out[:] = y * ganancia_total
        return out

    def _render_normal(self, frames, rate):
        """Camino de siempre (sin cambio de tonalidad): lectura directa, o estirado WSOLA si el tempo se aparta lo suficiente de 1,0."""
        try:
            if Deck.WSOLA and self._ws_usar(rate):
                y = self._render_wsola(frames, rate)
            elif self._ws_activo:
                if self._ws_exp == (self.cursor, self._cursor_frac):
                    h0 = self._ws.head
                    y_ws, _ = self._ws.leer(self.audio, frames, float(np.clip(rate, 0.5, 2.0)))
                    self.cursor = int(h0); self._cursor_frac = h0 - self.cursor
                    y_d = self._render_directo(frames, rate)
                    f = np.linspace(0.0, 1.0, frames, dtype=np.float32)[:, None]
                    y = y_ws * (1.0 - f) + y_d * f
                else:
                    y = self._render_directo(frames, rate)
                self._ws_activo = False
            else:
                y = self._render_directo(frames, rate)
        except Exception:
            import traceback; traceback.print_exc()
            Deck.WSOLA = False
            self._ws_activo = False
            print("[audio] WSOLA desactivado por un error: se usa el remuestreo")
            y = self._render_directo(frames, rate)
        return y

    def set_semitonos(self, s):
        """Sube o baja la tonalidad del tema `s` semitonos SIN cambiar su tempo (0 = original). Se vuelve a 0 al vaciar, cargar o detener el deck."""
        s = int(s)
        self.semitonos = s
        self.pitch = 2.0 ** (s / 12.0) if s else 1.0

    def _sinc_leer(self, src, pos):
        """Lee `src` (n,2) en las posiciones fraccionarias `pos` con el interpolador sinc de alta calidad."""
        n = len(src)
        tab = Deck._tabla_sinc()
        i1 = np.floor(pos).astype(np.int64)
        fr = (pos - i1) * Deck.SINC_FASES
        j = np.floor(fr).astype(np.int64)
        w = (fr - j).astype(np.float32)[:, None]
        H = tab[j] * (1.0 - w) + tab[j + 1] * w
        idx = np.clip(i1[:, None] + Deck._SINC_K[None, :], 0, n - 1)
        return np.einsum("nt,ntc->nc", H, src[idx]).astype(np.float32)

    def _render_tono(self, frames, rate):
        """Cambio de tonalidad con el tempo intacto: se estira con WSOLA a rate/pitch (tono original) y esa salida se lee a razón `pitch`
        (el remuestreo sube o baja el tono), de modo que por muestra de salida el tema avanza `rate` como siempre."""
        p = float(np.clip(self.pitch, 0.5, 2.0))
        rp = float(np.clip(rate / p, 0.5, 2.0))
        pos_nom = self.cursor + self._cursor_frac
        if not self._pt_ok or abs(pos_nom - self._pt_pos) > 4.0:            # arranque o salto de posición
            self._ws.reset(pos_nom)
            self._pt_buf = np.zeros((16, 2), dtype=np.float32)
            self._pt_ph = 16.0
            self._pt_ok = True
            self._pt_fi = 0
        buf, ph = self._pt_buf, self._pt_ph
        need = int(ph + (frames - 1) * p) + 18
        fin = False
        while len(buf) < need and not fin:
            y, fin = self._ws.leer(self.audio, max(need - len(buf), 512), rp)
            buf = np.concatenate((buf, y))
        out = self._sinc_leer(buf, ph + np.arange(frames, dtype=np.float64) * p)
        if self._pt_fi < 128:
            k = min(128 - self._pt_fi, frames)
            out[:k] *= ((np.arange(k, dtype=np.float32) + self._pt_fi + 1.0) / 128.0)[:, None]
            self._pt_fi += k
        ph += frames * p
        drop = max(0, int(ph) - 16)
        buf = buf[drop:]; ph -= drop
        self._pt_buf, self._pt_ph = buf, ph
        cv = self._ws.head - (len(buf) - ph) * rp                               # posición real del próximo sonido (descontando lo que quedó en el búfer)
        cv = max(0.0, cv)
        self.cursor = int(cv); self._cursor_frac = cv - self.cursor
        self._pt_pos = cv
        if (fin and ph >= len(buf) - 17) or self.cursor >= len(self.audio) - 2:
            self.playing = False
        return out

    LOOP_FADE = 48

    def render(self, frames):
        if self.loop is None and not self._loop_salir:
            return self._render_bloque(frames)
        return self._render_loop(frames)

    def iniciar_loop(self, n_beats):
        if self.audio is None or not self.analysis:
            return False
        b = np.asarray(self.analysis.get("beats_full") or self.analysis.get("beats") or [], dtype=np.float64)
        if len(b) < 12:
            return False
        with self.lock:
            pos = (self.cursor + self._cursor_frac) / self.sr
            i = int(np.argmin(np.abs(b - pos)))
            ini = float(b[i])
            if i + n_beats < len(b):
                fin = float(b[i + n_beats])
            else:
                paso = float(np.median(np.diff(b[max(0, i - 8):i + 1]))) if i >= 2 else 60.0 / max(60.0, float(self.analysis.get("bpm", 120)))
                fin = ini + n_beats * paso
            if fin * self.sr >= len(self.audio) - 2 or fin - ini < 0.05:
                return False
            self.loop = (ini * self.sr, fin * self.sr)
            self.loop_desfase = 0.0
            self._loop_salir = False
        return True

    def terminar_loop(self):
        if self.loop is None:
            return
        if self.playing:
            self._loop_salir = True
        else:
            self._saltar_a_virtual()

    def _saltar_a_virtual(self):
        with self.lock:
            if self.audio is not None:
                virt = min(self.cursor + self._cursor_frac + self.loop_desfase, len(self.audio) - 2.0)
                self.cursor = int(virt); self._cursor_frac = virt - self.cursor
            self.loop = None; self.loop_desfase = 0.0; self._loop_salir = False

    def _aplicar_fi(self, y, F):
        if self._fi_pend:
            k = min(F - self._fi_pos, len(y))
            if k > 0:
                y[:k] *= ((np.arange(k, dtype=np.float32) + self._fi_pos + 1.0) / F)[:, None]
                self._fi_pos += k
            if self._fi_pos >= F:
                self._fi_pend = False; self._fi_pos = 0
        return y

    def _tramo_loop(self, n, F):
        out = np.zeros((n, 2), dtype=np.float32)
        h = 0; vueltas = 0
        while h < n and vueltas < 12:
            vueltas += 1
            with self.lock:
                lp = self.loop
                if lp is None or not self.playing or self.audio is None:
                    break
                pos = self.cursor + self._cursor_frac
                rate = max(1e-6, self.rate_eff)
            ini, fin = lp
            m = int(np.ceil(max(0.0, fin - pos) / rate)); m = max(1, min(m, n - h))
            y = self._render_bloque(m)
            self._aplicar_fi(y, F)
            out[h:h + m] = y; h += m
            with self.lock:
                if self.loop is None:
                    break
                pos2 = self.cursor + self._cursor_frac
                if pos2 >= fin - 2.0 * rate:
                    over = max(0.0, pos2 - fin)
                    nueva = ini + over
                    self.cursor = int(nueva); self._cursor_frac = nueva - self.cursor
                    self.loop_desfase += (fin - ini)
                    k = min(F, h)
                    if k > 0:
                        out[h - k:h] *= np.linspace(1.0, 0.0, k, dtype=np.float32)[:, None]
                    self._fi_pend = True; self._fi_pos = 0
        if h < n:
            y = self._render_bloque(n - h)
            self._aplicar_fi(y, F)
            out[h:] = y
        return out

    def _render_loop(self, frames):
        F = max(8, min(self.LOOP_FADE, frames // 4))
        if self._loop_salir and self.loop is not None:
            self._loop_salir = False
            out = np.zeros((frames, 2), dtype=np.float32)
            out[:F] = self._tramo_loop(F, F) * np.linspace(1.0, 0.0, F, dtype=np.float32)[:, None]
            self._saltar_a_virtual()
            self._fi_pend = True; self._fi_pos = 0
            y = self._render_bloque(frames - F)
            out[F:] = self._aplicar_fi(y, F)
            return out
        self._loop_salir = False
        return self._tramo_loop(frames, F)
