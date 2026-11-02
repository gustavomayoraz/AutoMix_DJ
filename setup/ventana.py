"""AutoMix — ventana principal de AutoMix (la arman las partes ventana_*.py)."""
import sys
import threading
import time
import numpy as np
import sounddevice as sd
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QSizePolicy, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtCore import QTimer, Qt
from setup.ajustes import ANCHO_LISTA, ESC_MIN, fmt_time
from setup.analisis import _cerrar_pool
from setup.barra import BarraProgresoConMarca
from setup.base import CONSOLA, log
from setup.biblioteca import Biblioteca
from setup import almacen, cruce_auto
from setup.config import DEF_AGUDOS, DEF_GOLPE, DEF_MARCA, DEF_MB_INT, DEF_MB_LIM, DEF_MB_VEL, DEF_MEDIOS, DEF_VOLUMEN, DEF_ZOOM, DEF_PUNTO_A, DEF_PUNTO_B, DEF_PUNTO_S, DEF_RAMPA_B, DEF_FIN_A, DEF_GAMMA_A, guardar_config, leer_config
from setup.danados import HistorialDanados
from setup.deck import Deck
from setup.dialogo_danados import DialogoDanados
from setup.mezclador import GlobalMixer
from setup.panel_cruce import PanelCruce
from setup.tema import COLOR_DECK_A, COLOR_DECK_B, TEMA, aplicar_tema
from setup.timeline import UnifiedTimeline
from setup.widgets import EtiquetaElidida, LogoAutoMix, BotonConfig, _OpcionApagada, qss_marco
from setup.ventana_multibanda import MultibandaMixin
from setup.ventana_auto import AutomaticoMixin
from setup.ventana_auriculares import AuricularesMixin
from setup.ventana_biblioteca import BibliotecaMixin
from setup.ventana_efectos import EfectosMixin
from setup.ventana_escala import EscalaMixin
from setup.ventana_grabacion import GrabacionMixin
from setup.ventana_loop import LoopMixin
from setup.ventana_rejilla import RejillaMixin
from setup.ventana_sync import SyncMixin
from setup.ventana_mezcla import MezclaMixin


class AutoMix(MultibandaMixin, EfectosMixin, LoopMixin, AuricularesMixin, AutomaticoMixin, BibliotecaMixin, EscalaMixin, GrabacionMixin, RejillaMixin, SyncMixin, MezclaMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        try:
            aplicar_tema(QApplication.instance())
        except Exception:
            import traceback; traceback.print_exc()
        cfg = leer_config()
        self._modo_orden = cfg.get("lib_orden", "bpm")             # 'estilo' = la lista quedó ordenada por 🎵 Ordenar por estilo: no se reordena sola
        from setup.ajustes_cruce import cargar as _cargar_ajustes_cruce
        _cargar_ajustes_cruce()                                   # tiempos del cruce guardados (⚙ Ajustes)
        self.punto_a = float(cfg.get("punto_a", DEF_PUNTO_A))
        self.punto_b = float(cfg.get("punto_b", DEF_PUNTO_B))
        self.cruce_ab = bool(cfg.get("cruce_ab", True))
        self.punto_s = float(cfg.get("punto_s", DEF_PUNTO_S))
        self.rampa_b = float(cfg.get("rampa_b", DEF_RAMPA_B))
        self.fin_a = float(cfg.get("fin_a", DEF_FIN_A))
        self.gamma_a = float(cfg.get("gamma_a", DEF_GAMMA_A))
        self.cruce_bandas = bool(cfg.get("cruce_bandas", True))
        self.cruce_auto = bool(cfg.get("cruce_auto", False))      # False = cruce manual (los deslizadores) · True = Auto (lo diseña el programa)
        self.comp_nivel = bool(cfg.get("comp_nivel", True))
        self._plan = None                 # diseño del cruce Auto de la mezcla en curso (dict) o None
        self._ultimo_plan = None          # el último que se diseñó (el gráfico lo muestra mientras no hay mezcla)
        self._cf_dir = "AB"
        self.carga_auto = True
        self.auto_s = int(cfg.get("auto_s", 30))
        self._fader_prev = None
        self._carga_pendiente = False
        self.historial_danados = HistorialDanados()          # temas dañados (se guarda en cache/temas_danados.json)
        self._danados = self.historial_danados.claves()      # sus claves: la carga automática y el Random los saltean
        self._carga_auto_path = None       # último tema que cargó solo la carga automática (si ese falla, se carga otro)
        self._mix_pausa_hecha = False
        self._marca_frac = float(cfg.get("marca_frac", DEF_MARCA))
        if self._marca_frac < 0:
            self._marca_frac = DEF_MARCA
        self._marca_disparada = False
        self._ultimo_prog_primario = 0.0
        self._ultimo_deck_primario = None
        self.setWindowTitle("AutoMix 🎚️")
        self.resize(1560, 850)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.split = split
        self.setCentralWidget(split)

        panel_lib = QWidget()
        lib_lay = QVBoxLayout(panel_lib)
        lib_lay.setContentsMargins(8, 8, 0, 8)
        lib_lay.setSpacing(4)
        self.logo = LogoAutoMix(lambda: getattr(getattr(self, "mixer", None), "nivel_vis", 0.0))
        lib_lay.addWidget(self.logo)
        self.lbl_lib = EtiquetaElidida("📁 Biblioteca — arrastrá carpetas acá")
        self.lbl_lib.setStyleSheet(f"color:{TEMA['texto']}; font-weight:bold; font-size:15px;")
        lib_lay.addWidget(self.lbl_lib)
        # ---- BUSCADOR + BOTÓN << (contraer todas las carpetas) ----
        self.ed_filtro = QLineEdit()
        self.ed_filtro.setPlaceholderText("Buscar…")
        self.ed_filtro.setClearButtonEnabled(True)
        self.btn_plegar = QPushButton("<<")
        self.btn_plegar.setFixedSize(34, 28)
        self.btn_plegar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_plegar.setStyleSheet("QPushButton { padding:0px; font-size:13px; font-weight:bold; }")
        self.btn_plegar.setToolTip("Contraer todas las carpetas (activa «Por carpetas» si hace falta)")
        self.btn_plegar.clicked.connect(self._toggle_plegar_carpetas)
        fila_busq = QHBoxLayout()
        fila_busq.setContentsMargins(0, 0, 0, 0)
        fila_busq.setSpacing(4)
        fila_busq.addWidget(self.ed_filtro, 1)          # el buscador se estira, el botón no
        fila_busq.addWidget(self.btn_plegar, 0)         # el botón siempre visible, con ancho fijo
        lib_lay.addLayout(fila_busq)
        # -------------------------------------------------------------
        self.biblioteca = Biblioteca()
        self.biblioteca.setStyleSheet(qss_marco("QListWidget", "#2bb5d6", titulo=False))     # el fondo de la lista, con el mismo degradé de color
        lib_lay.addWidget(self.biblioteca, stretch=1)
        lib_btns = QHBoxLayout()
        b_limpiar = QPushButton("Vaciar"); b_limpiar.clicked.connect(self.biblioteca.limpiar)
        b_limpiar.setStyleSheet("QPushButton { border:1px solid #ff4d4d; border-radius:9px; } QPushButton:hover { border:1px solid #ff8585; border-radius:9px; }")      # contorno rojo (como el amarillo de «Por carpetas»)
        self.btn_ordenar = QPushButton("🔃 Ordenar por BPM")
        self.btn_ordenar.clicked.connect(self._ordenar_biblioteca_bpm)
        self.btn_reanalizar = QPushButton("🔄 Re-analizar")
        self.btn_reanalizar.setToolTip("Si hay temas sin analizar, los analiza a máxima velocidad (ventanita con el avance, el reproductor se oculta); si no hay pendientes no hace nada (Ctrl + clic = volver a analizar TODO desde cero)")
        self.btn_reanalizar.clicked.connect(self._reanalizar_biblioteca)
        lib_btns.addWidget(b_limpiar); lib_btns.addWidget(self.btn_ordenar)
        lib_btns.addWidget(self.btn_reanalizar)
        lib_lay.addLayout(lib_btns)
        self.btn_random = QPushButton("➡ Normal")
        self.btn_random.setCheckable(True)
        self.btn_random.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_random.setToolTip("Cómo se elige el tema que sigue.\n➡ Normal: el que sigue en la lista.\n"
                                   "🎲 Random: uno al azar de la lista, ya analizado y con el mismo BPM que el que suena; si no hay, de a un BPM más cerca (±1, ±2…), "
                                   "así el tempo nunca se aleja mucho. No repite hasta tocar todos los de ese rango.")
        self.btn_random.setStyleSheet("QPushButton:checked { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffd35a, stop:1 #e08a00); color:#2a1700; font-weight:bold; border:1px solid #fff0b8; }")
        self.btn_random.toggled.connect(self._random_cambio)
        self.btn_agrupar = QPushButton("📂 Por carpetas")
        self.btn_agrupar.setCheckable(True)
        self.btn_agrupar.setToolTip("Muestra la lista separada por carpetas: el nombre de la carpeta como título y debajo sus temas.\n"
                                    "Clic en un título para cerrar o abrir esa carpeta. Volvé a tocar el botón para la lista plana.")
        self.btn_agrupar.clicked.connect(self._alternar_agrupada)
        self.btn_danados = QPushButton("⚠")
        self.btn_danados.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_danados.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)       # botón chico: no le saca lugar a Random y Por carpetas
        self.btn_danados.setToolTip("⚠ Historial de temas dañados: archivos que no se pudieron cargar o analizar (la carga automática y el Random los saltean)")
        self.btn_danados.clicked.connect(self._abrir_danados)
        self._actualizar_btn_danados()
        self.btn_estadisticas = QPushButton("📊")
        self.btn_estadisticas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_estadisticas.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.btn_estadisticas.setToolTip("📊 Estadísticas: reproducciones, estrellas (automáticas, según cuánto suena cada tema) y los temas con 👍.\n"
                                         "El 👍 se marca con clic derecho sobre un tema de la lista.")
        self.btn_estadisticas.clicked.connect(self._abrir_estadisticas)
        fila_orden = QHBoxLayout(); fila_orden.addWidget(self.btn_random); fila_orden.addWidget(self.btn_agrupar); fila_orden.addWidget(self.btn_danados)
        fila_orden.addWidget(self.btn_estadisticas)
        lib_lay.addLayout(fila_orden)
        self.btn_estilo = QPushButton("🎵 Ordenar por estilo")
        self.btn_estilo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_estilo.setToolTip("Ordena la lista del tema más parecido al menos parecido (tempo, energía, voz, bajo, bombo), sin carpetas.\n"
                                   "Parte del tema que suena (o del que tengas elegido) y en cada paso sigue el más parecido.\n"
                                   "Usa lo que ya analizó AutoMix; la primera vez con muchos temas puede tardar un poco.")
        self.btn_estilo.clicked.connect(self._ordenar_por_estilo)
        self.btn_destildar = QPushButton("⬜ Destildar todos")
        self.btn_destildar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_destildar.setToolTip("Los temas que ya sonaron (más del 50 %) se tildan solos para que no se repitan.\n"
                                      "Este botón los destilda a todos. Los que tildaste a mano no se tocan.")
        self.btn_destildar.clicked.connect(self._destildar_tocados)
        fila_estilo = QHBoxLayout(); fila_estilo.addWidget(self.btn_estilo); fila_estilo.addWidget(self.btn_destildar)
        lib_lay.addLayout(fila_estilo)
        # 🎼 Armónico y 🥁 En frase se quitaron de la pantalla: quedan siempre apagados
        # (el siguiente tema es el que sigue en la lista, ordenada por BPM, y la mezcla automática arranca al llegar a la marca).
        self.chk_armonico = _OpcionApagada()
        self.chk_frases = _OpcionApagada()
        self.chk_wsola = QCheckBox("🎚 Tono fijo (WSOLA)")
        self.chk_wsola.setToolTip("Al cambiar la velocidad de un tema (tempo, enganche, mezcla) el tono NO cambia: el audio se estira con WSOLA.\n"
                                  "Destildado: la velocidad cambia el tono, como en un disco de vinilo.")
        self.chk_wsola.setChecked(bool(leer_config().get("wsola", True)))
        Deck.WSOLA = self.chk_wsola.isChecked()
        self.chk_wsola.toggled.connect(self._wsola_cambio)
        lib_lay.addWidget(EtiquetaElidida("Arrastrá un tema a Deck A o B"))
        self.ed_filtro.textChanged.connect(self._filtrar_biblioteca)
        self.biblioteca.cambio.connect(self._biblioteca_cambio)
        self.biblioteca.aviso.connect(self._set_status)
        self.biblioteca.itemDoubleClicked.connect(self._biblioteca_doble_clic)
        split.addWidget(panel_lib)

        central = QWidget()
        split.addWidget(central)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([ANCHO_LISTA, 1180])
        split.setChildrenCollapsible(False)
        panel_lib.setMinimumWidth(ANCHO_LISTA)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        decks_row = QHBoxLayout()
        decks_row.setSpacing(14)
        self.deck_a = Deck("A", COLOR_DECK_A, self._on_analysis)
        self.deck_b = Deck("B", COLOR_DECK_B, self._on_analysis)
        self.deck_a.on_ubicar_cb = self.deck_b.on_ubicar_cb = self._ubicar_en_lista
        self.deck_a.audio_fallo.connect(lambda p, d=self.deck_a: self._tema_danado(p, d))
        self.deck_b.audio_fallo.connect(lambda p, d=self.deck_b: self._tema_danado(p, d))
        self.deck_a.on_play_cb = self._auto_sync
        self.deck_b.on_play_cb = self._auto_sync
        # Callback para que el botón reset avise a la ventana y se redibuje el timeline
        self.deck_a.on_reset_cb = self._on_reset_deck
        self.deck_b.on_reset_cb = self._on_reset_deck
        self.panel_cruce = PanelCruce(self)
        izq = QVBoxLayout(); izq.setSpacing(6); izq.setContentsMargins(0, 0, 0, 0)
        self._col_central = izq       # columna central: decks (apilados) + tarjetas a su derecha
        fila_sup = QHBoxLayout(); fila_sup.setSpacing(14); fila_sup.setContentsMargins(0, 0, 0, 0)
        decks_ab = QVBoxLayout(); decks_ab.setSpacing(10); decks_ab.setContentsMargins(0, 0, 0, 0)       # Deck A arriba, Deck B abajo
        decks_ab.addWidget(self.deck_a, 1)       # los dos decks se estiran parejo hasta abajo (sin hueco vacío)
        decks_ab.addWidget(self.deck_b, 1)
        self.marco_dur = QFrame()                # barra de iconos (duración, teclas globales, consola, esquina, ajustes): debajo del Deck B, en su cuadro de color
        self.marco_dur.setObjectName("marcoDur")
        self.marco_dur.setStyleSheet(qss_marco("QFrame#marcoDur", TEMA["verde"], titulo=False))
        self._fila_duracion = QHBoxLayout(self.marco_dur); self._fila_duracion.setContentsMargins(18, 10, 18, 10); self._fila_duracion.setSpacing(10)
        self.marco_dur.setMinimumHeight(64)
        decks_ab.addWidget(self.marco_dur)
        fila_sup.addLayout(decks_ab, 5)
        der_c = QVBoxLayout(); der_c.setSpacing(8); der_c.setContentsMargins(0, 0, 0, 0)       # a la derecha de los decks: cuadrícula de tarjetas + barra de iconos
        self._fila_herr = QGridLayout(); self._fila_herr.setContentsMargins(0, 0, 0, 0); self._fila_herr.setHorizontalSpacing(10); self._fila_herr.setVerticalSpacing(6)
        self._fila_herr.setColumnStretch(0, 1); self._fila_herr.setColumnStretch(1, 1)
        self._fila_herr.setRowStretch(0, 1); self._fila_herr.setRowStretch(1, 1)
        der_c.addLayout(self._fila_herr, 1)      # 2×2: 🛠 Herramientas · ⏺ Grabación / 🎧 Auriculares · 🎬 Mezcla (repartidas parejo)
        fila_sup.addLayout(der_c, 6)
        izq.addLayout(fila_sup)
        self.marco_rej = QGroupBox("🛠 Herramientas")          # tarjeta cian: Auto-grilla + tono fijo y efectos
        self.marco_rej.setObjectName("marcoRej")
        self.marco_rej.setStyleSheet(qss_marco("QGroupBox#marcoRej", TEMA["cian"]))
        self._fila_rej = QVBoxLayout(self.marco_rej); self._fila_rej.setContentsMargins(10, 6, 10, 8); self._fila_rej.setSpacing(6)
        self._fila_herr.addWidget(self.marco_rej, 0, 0)
        izq.addStretch(1)
        decks_row.addLayout(izq, stretch=1)
        decks_row.addWidget(self.panel_cruce, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(decks_row)

        self.mixer = GlobalMixer(self.deck_a, self.deck_b, sr=44100, blocksize=1024)
        self.mixer.comp_activo = self.comp_nivel
        self.mixer.iniciar(leer_config().get("salida_principal") or None)      # la salida principal elegida (si no, la predeterminada de Windows)

        cf_row = QHBoxLayout()           # el crossfader va en el panel de la derecha de los deslizadores
        self._cf_row = cf_row
        cf_row.addWidget(QLabel("A"))
        self.crossfader = QSlider(Qt.Orientation.Horizontal)
        self.crossfader.setRange(0, 100)
        self.crossfader.setValue(0)
        self.crossfader.valueChanged.connect(self._update_cf)
        cf_row.addWidget(self.crossfader, stretch=1)
        cf_row.addWidget(QLabel("B"))
        self.sld_master = QSlider(Qt.Orientation.Horizontal)
        self.sld_master.setRange(0, 100)
        self.sld_master.setMinimumWidth(90)
        self.sld_master.setValue(int(leer_config().get("vol_general", DEF_VOLUMEN)))
        self.lbl_master = QLabel(f"{self.sld_master.value()}%")
        self.lbl_master.setMinimumWidth(38)
        self.sld_master.valueChanged.connect(self._vol_general_cambio)
        self.mixer.set_volumen(self.sld_master.value() / 100.0)
        self.mixer._gain_act = self.mixer.gain_obj

        ctrl_box = QGroupBox("🎛️ Controles")
        ctrl_box.setFixedHeight(60)
        c = QHBoxLayout(ctrl_box)
        c.setContentsMargins(8, 4, 8, 4)

        self.btn_mix = QPushButton("🎬 Mezclar (F2)")
        self.btn_mix.setFixedHeight(30)
        self.btn_mix.clicked.connect(self._mix_clicked)
        self.shortcut_f2 = QShortcut(QKeySequence(Qt.Key.Key_F2), self)
        self.shortcut_f2.activated.connect(self._mix_clicked)

        self.shortcut_espacio = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.shortcut_espacio.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_espacio.activated.connect(self._toggle_play_pausa)

        self.lbl_dur = QLabel("Duración")
        self.spin_mix = QSpinBox()
        self.spin_mix.setRange(1, 120)
        self.spin_mix.setSuffix(" s")
        self.spin_mix.setFixedHeight(30)
        self.spin_mix.setValue(self._cargar_duracion_mezcla())
        self.btn_mix.setText(self._texto_btn_mix())
        self.spin_mix.setEnabled(self._spin_mix_activo())
        self.spin_mix.setToolTip("Duración del cruce manual. En modo Auto la decide el programa en cada mezcla.")
        self.spin_mix.valueChanged.connect(self._duracion_mezcla_cambio)
        c.addStretch()

        contenedor_inferior = QWidget()
        ci_layout = QVBoxLayout(contenedor_inferior)
        ci_layout.setContentsMargins(0, 0, 0, 0)
        ci_layout.setSpacing(6)
        ci_layout.addWidget(self._crear_panel_multibanda())
        root.addWidget(contenedor_inferior)

        self.lbl_status = EtiquetaElidida("Listo con Auto-Gain activado. Espacio = pausa/play.")
        self.lbl_status.setStyleSheet(
            "color:#9fe; background:#111; padding:4px; "
            "border:1px solid #333; border-radius:4px; font-size:11px;"
        )
        self.lbl_status.setFixedHeight(24)
        fila_estado = QHBoxLayout()
        fila_estado.setContentsMargins(0, 0, 0, 0)
        fila_estado.setSpacing(4)
        self.lbl_status.setVisible(False)        # la barra negra de estado ya no se muestra (el texto sigue disponible por tooltip)
        fila_estado.addStretch(1)
        self.btn_consola = QPushButton("🖥")
        self.btn_consola.setCheckable(True)
        self.btn_consola.setFixedSize(50, 34)
        self.btn_consola.setStyleSheet("QPushButton { font-size:19px; padding:0px; }")
        self.btn_consola.setToolTip("Mostrar / ocultar la consola (todo lo que hace el programa, con hora)")
        self.btn_consola.clicked.connect(self._alternar_consola)
        self._t_consola = QTimer(self); self._t_consola.setInterval(600)       # si la cerraron con la X, el botón se suelta
        self._t_consola.timeout.connect(lambda: self.btn_consola.setChecked(CONSOLA.sondear()) if CONSOLA._modo != "terminal" else None)
        self._t_consola.start()
        if not CONSOLA.es_windows:
            self.btn_consola.setVisible(False)
        self._f_dur.insertWidget(self._f_dur.count() - 1, self.btn_consola)   # al costado de Duración, mismo tamaño
        self.btn_grabar = QPushButton("⏺ Grabar mezcla")
        self.btn_grabar.setFixedHeight(34)
        self._QSS_GRAB = ("QPushButton { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ff7a86, stop:1 #e0283c); color:#ffffff; font-weight:bold;"
                          " border:1px solid #ffb3bb; border-radius:9px; }"
                          "QPushButton:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ff95a0, stop:1 #f03a4e); }"
                          "QPushButton:pressed { background:#b81e2f; padding-top:2px; }")
        self.btn_grabar.setStyleSheet(self._QSS_GRAB)           # rojo de "REC" (al grabar pasa a rojo oscuro con borde brillante)
        self._poner_formato_grab_pendiente = leer_config().get("grab_formato", "mp3")      # (se aplica abajo, cuando ya existe el botón de formato)
        self.btn_grabar.clicked.connect(self._alternar_grabacion)
        self.btn_fmt_grab = QPushButton("MP3")                  # formato de la grabación: MP3 (320 kbps) o WAV (sin pérdida)
        self.btn_fmt_grab.setFixedSize(76, 38); self.btn_fmt_grab.setStyleSheet("QPushButton { padding:0px; font-size:17px; font-weight:bold; }")
        self.btn_fmt_grab.setToolTip("Formato de la grabación: clic para cambiar entre MP3 (320 kbps, ocupa poco) y WAV (sin pérdida, ocupa mucho)")
        self.btn_fmt_grab.clicked.connect(self._cambiar_formato_grab)
        self._poner_formato_grab(self._poner_formato_grab_pendiente)
        self.btn_carpeta_grab = QPushButton("📂")
        self.btn_carpeta_grab.setFixedSize(60, 38); self.btn_carpeta_grab.setStyleSheet("QPushButton { padding:0px; font-size:26px; }")
        self.btn_carpeta_grab.setToolTip("Abrir la carpeta con las mezclas grabadas")
        self.btn_carpeta_grab.clicked.connect(self._abrir_carpeta_grabaciones)
        self.lbl_grab = EtiquetaElidida("")
        self.lbl_grab.setFixedHeight(28); self.lbl_grab.setStyleSheet("color:#9fe3c6; font-size:11px;")   # (el nombre del archivo guardado)
        self.btn_auto_rej = QPushButton("🎯 Auto")
        self.btn_auto_rej.setCheckable(True)
        self.btn_auto_rej.setFixedHeight(28); self.btn_auto_rej.setMinimumWidth(92)
        self.btn_auto_rej.setToolTip("Auto-grilla (verde = activada, rojo = desactivada).\nActivada: revisa sin parar los temas que entran, "
                                     "corrige el BPM doble/mitad, centra la grilla en los bombos, encuentra solo dónde empieza el compás (el 1) "
                                     "y los empareja (tempo + compases). Ya no hay nada que ajustar a mano.")
        self.btn_auto_rej.setChecked(bool(leer_config().get("auto_grilla", True)))
        self.chk_wsola.setFixedHeight(28)
        # fila 1: Auto + Grabar mezcla (ancho) + 📂 · fila 2: tono fijo (y el nombre del .wav guardado) · fila 3: efectos de sonido
        fila_au = QHBoxLayout(); fila_au.setContentsMargins(0, 0, 0, 0); fila_au.setSpacing(10)
        fila_au.addWidget(self.btn_auto_rej); fila_au.addWidget(self.chk_wsola, 1)          # fila 1: Auto + tono fijo
        self._fila_rej.addLayout(fila_au)
        # tarjeta propia ⏺ Grabación (roja), entre Herramientas y Auriculares
        self.marco_grab = QGroupBox("⏺ Grabación")
        self.marco_grab.setObjectName("marcoGrab")
        self.marco_grab.setStyleSheet(qss_marco("QGroupBox#marcoGrab", TEMA["rojo"]))
        col_g = QVBoxLayout(self.marco_grab); col_g.setContentsMargins(10, 6, 10, 8); col_g.setSpacing(6)
        col_g.addWidget(self.btn_grabar)
        fila_g = QHBoxLayout(); fila_g.setSpacing(6); fila_g.setContentsMargins(0, 0, 0, 0)
        fila_g.addWidget(self.btn_fmt_grab); fila_g.addWidget(self.btn_carpeta_grab); fila_g.addWidget(self.lbl_grab, 1)
        col_g.addLayout(fila_g)
        col_g.addStretch(1)
        self._fila_herr.addWidget(self.marco_grab, 0, 1)
        self._fila_rej.addWidget(self._crear_efectos())          # 🎆 efectos de sonido (lista + ▶ + volumen)
        self._fila_rej.addStretch(1)
        self._crear_auriculares()                                # tarjeta 🎧 (se acomoda entre Herramientas y Mezcla)
        self._t_grab = QTimer(self); self._t_grab.setInterval(250)
        self._t_grab.timeout.connect(self._tic_grabacion)
        for _b in (self.btn_auto_rej, self.btn_grabar, self.btn_carpeta_grab):
            _b.setFocusPolicy(Qt.FocusPolicy.NoFocus)         # que la barra espaciadora / Enter nunca los active por accidente
        self._estilo_auto_rej()
        self.btn_auto_rej.toggled.connect(self._auto_rej_cambio)
        self._rej_visto = {}
        self._t_auto_rej = QTimer(self); self._t_auto_rej.setInterval(800)
        self._t_auto_rej.timeout.connect(self._vigilar_rejilla)
        if self.btn_auto_rej.isChecked():
            self._t_auto_rej.start()
        self.lbl_rej = QLabel(""); self.lbl_rej.setWordWrap(True); self.lbl_rej.setStyleSheet("color:#9fe3c6; font-size:12px;")
        izq.addWidget(self.lbl_rej)               # (oculto: ya no se muestra lo que encontró el botón Auto; queda en la consola)
        self.lbl_rej.hide()
        self.btn_esquina = QPushButton("↖")
        self.btn_esquina.setFixedSize(50, 34)
        self.btn_esquina.setStyleSheet("QPushButton { font-size:21px; padding:0px; }")
        self.btn_esquina.setToolTip("Ventana al tamaño mínimo, en la esquina superior izquierda de la pantalla")
        self.btn_esquina.clicked.connect(self._a_la_esquina)
        self._f_dur.insertSpacing(self._f_dur.count() - 1, 12)
        self._f_dur.insertWidget(self._f_dur.count() - 1, self.btn_esquina)   # al lado de la consola
        self.btn_quitar_espera = QPushButton("⏏")
        self.btn_quitar_espera.setFixedSize(50, 34)
        self.btn_quitar_espera.setStyleSheet("QPushButton { font-size:21px; padding:0px; }")
        self.btn_quitar_espera.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_quitar_espera.setToolTip("Quitar el tema que espera: deja vacío el deck que no suena, así el tema actual termina y el reproductor "
                                          "se detiene solo (no mezcla ni carga otro). Si no suena ninguno, vacía los dos.")
        self.btn_quitar_espera.clicked.connect(self._quitar_el_que_espera)
        self._f_dur.insertSpacing(self._f_dur.count() - 1, 12)
        self._f_dur.insertWidget(self._f_dur.count() - 1, self.btn_quitar_espera)   # a la derecha de la flecha ↖
        self.btn_ajustes_gen = BotonConfig("⚙", px=24)              # botón animado (colores que fluyen): se nota que es de configuración
        self.btn_ajustes_gen.setFixedSize(50, 34)
        
        self.btn_ajustes_gen.setToolTip("Ajustes generales: todos los tiempos del cruce (pausa del medio, frenado del crossfader, duración y entrada de B del Auto)")
        self.btn_ajustes_gen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_ajustes_gen.clicked.connect(self._abrir_ajustes_cruce)
        self._f_dur.insertSpacing(self._f_dur.count() - 1, 12)
        self._f_dur.insertWidget(self._f_dur.count() - 1, self.btn_ajustes_gen)   # a la derecha de la flecha ↖

        barra_box = QGroupBox("⚓ Auto-enganche — progreso del tema sonando")
        barra_box.setFixedHeight(92)
        barra_box.setStyleSheet(qss_marco("QGroupBox", TEMA["ambar"]) + " QGroupBox { padding-top:0px; }")     # sin relleno arriba: la barra puede subir hasta el título
        bl = QHBoxLayout(barra_box)
        bl.setContentsMargins(8, 0, 8, 2)
        bl.setSpacing(8)
        self.barra_prog = BarraProgresoConMarca()
        self.barra_prog.set_marca(self._marca_frac)
        self.barra_prog.marca_cambiada.connect(self._marca_cambiada)
        self.barra_prog.marca_cruzada.connect(self._marca_cruzada)
        bl.addWidget(self.barra_prog, stretch=1)
        self.chk_break = QCheckBox("🌫 Mezclar\nen break")                 # atajo al ajuste «Detectar breaks» (⚙): se enciende/apaga sin abrir los ajustes
        self.chk_break.setToolTip("Si el tema que suena entra en un break (energía baja) antes de la marca, se mezcla ahí sin esperar a la marca.\nLos detalles (desde dónde, qué tan bajo, cuánto dura) se afinan en la tarjeta 🌫 Break de la derecha.")
        self.chk_break.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chk_break.setChecked(cruce_auto.BREAK_ACTIVO >= 0.5)
        self.chk_break.toggled.connect(self._break_tilde)
        bl.addWidget(self.chk_break)
        fila_barra = QHBoxLayout(); fila_barra.setSpacing(8); fila_barra.setContentsMargins(0, 0, 0, 0)
        fila_barra.addWidget(barra_box, 1)
        fila_barra.addWidget(self._crear_break())              # 🌫 Break: los tres deslizadores del detector, a la derecha de la barra
        self._col_central.insertLayout(1, fila_barra)        # el Auto-enganche va justo debajo de los decks y las tarjetas (en el hueco de la columna central), al lado del panel de cruce

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Zoom (px/s):"))
        self.sld_zoom = QSlider(Qt.Orientation.Horizontal)
        self.sld_zoom.setRange(5, 300)
        self.sld_zoom.setValue(DEF_ZOOM)
        self.sld_zoom.valueChanged.connect(self._update_zoom)
        zoom_row.addWidget(self.sld_zoom, stretch=1)
        self.lbl_zoom = QLabel(str(DEF_ZOOM))
        self.lbl_zoom.setFixedWidth(50)
        zoom_row.addWidget(self.lbl_zoom)
        root.addLayout(zoom_row)

        tl_box = QGroupBox("🎼 Timeline — Playhead FIJO al centro")
        tl_box.setStyleSheet(qss_marco("QGroupBox", TEMA["verde"]))
        tl_lay = QVBoxLayout(tl_box)
        tl_lay.setContentsMargins(4, 4, 4, 4)
        self.timeline = UnifiedTimeline(self.deck_a, self.deck_b, on_manito_cb=self._on_manito_release)
        self.timeline.deck_vigilado = self._deck_primario          # el timeline dibuja las líneas del detector de breaks solo en el canal del tema que suena
        self.timeline.px_per_s = float(self.sld_zoom.value())
        tl_lay.addWidget(self.timeline)
        root.addWidget(tl_box, stretch=1)

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)

        self.mix_timer = QTimer()
        self.mix_timer.timeout.connect(self._mix_step)
        self.mix_steps_total = 0
        self.mix_steps_done = 0
        self._mix_dur = 0.0; self._mix_acum = 0.0; self._mix_tref = 0.0     # la mezcla se mide con el reloj real
        self.mix_from = 0
        self.mix_to = 0

        self.mix_pausa_timer = QTimer(self)
        self.mix_pausa_timer.setSingleShot(True)
        self.mix_pausa_timer.timeout.connect(self._reanudar_mix)

        self._target_rate_a = None
        self._target_rate_b = None
        self._alpha_a = 0.05
        self._alpha_b = 0.05
        self._smooth_timer = QTimer()
        self._smooth_timer.timeout.connect(self._smooth_tempo)
        self._smooth_timer.start(20)

        self.magnet_on = False
        self._err_filtrado = 0.0
        self._sync_I = 0.0
        self._sync_t = None
        self._sync_n = 0
        self._sync_hist = []
        self.master_for_magnet = None
        self.slave_for_magnet = None
        self.magnet_timer = QTimer()
        self.magnet_timer.timeout.connect(self._magnet_correct)

        self.manito_on = True

        self.biblioteca.primera_tanda.connect(self._restaurar_decks)                 # los decks guardados vuelven apenas hay temas en la lista
        self.biblioteca.carga_progreso.connect(lambda h, n: self.lbl_lib.setText(f"📁 Cargando biblioteca… {h}/{n}") if h < n else None)
        self.biblioteca.carga_terminada.connect(self._post_carga_biblioteca)
        self.biblioteca.lista_cargada.connect(self._plegar_al_inicio)
        self.biblioteca.cargar()                                                     # (en tandas: la ventana se abre enseguida)
        self.btn_agrupar.setChecked(True); self.biblioteca.set_agrupado(True)      # siempre se arranca en vista por carpetas (y plegadas: ver _post_carga_biblioteca)
        self._update_cf()

        self._esc_actual = 1.0
        self._ancho_lista_base = None                         # None = el ancho de siempre (ANCHO_LISTA); si el usuario mueve el divisor, se recuerda
        self._esc_timer = QTimer(self)
        self._esc_timer.setSingleShot(True)
        self._esc_timer.timeout.connect(self._escalar_segun_ventana)
        self._registrar_escala()
        self.split.splitterMoved.connect(self._splitter_movido)

        # Tamaño "natural" (escala 1.0) = lo que mide el contenido de verdad. La escala sale de
        # comparar la ventana con ese tamaño, así el contenido SIEMPRE entra en la ventana
        # (antes se usaba un 1560x850 fijo y, al agregar paneles, el mínimo pasaba la pantalla).
        # El tamaño inicial es el MÍNIMO que soporta el formulario con las letras ya en su tamaño normal
        # (escala 1.0): ni más grande, ni con letras diminutas.
        self._ajustar_escala_pantalla()                   # escala mínima: 0,6 normalmente; menor (o modo compacto) si la pantalla es chica (ej. 1366x768)
        try:                                                  # nace ya en la esquina superior izquierda (antes de mostrarse): sin salto desde el centro
            _ag = (self.screen() or QApplication.primaryScreen()).availableGeometry()
            self.move(_ag.left() + 4, _ag.top() + 4)
        except Exception:
            pass
        QTimer.singleShot(0, self._a_la_esquina)

        self.biblioteca.nuevos.connect(lambda: QTimer.singleShot(1500, self._auto_analizar))
        self._ordenar_lista()                                 # al abrir: lista ordenada (cadena armónica/frase o, si no, por BPM)
        self._ordenar_al_terminar = True                      # y otra vez cuando termine de analizar lo que falte
        QTimer.singleShot(2500, self._auto_analizar)

    def _wsola_cambio(self, v):
        Deck.WSOLA = bool(v)
        guardar_config(wsola=bool(v))
        log("audio", "tono fijo (WSOLA) " + ("activado" if v else "desactivado: la velocidad cambia el tono"))

    def _alternar_consola(self):
        visible = CONSOLA.alternar()
        self.btn_consola.setChecked(visible)
        if CONSOLA._modo == "terminal":
            self._set_status("🖥 Lo abriste desde una terminal: ahí mismo ves todo lo que hace")
            return
        self._set_status("🖥 Consola abierta (se cierra con este botón o con su X)" if visible else "🖥 Consola oculta")

    def _set_status(self, text):
        if text != getattr(self, "_ult_status_log", None):          # lo que el programa "dice" también va a la consola
            self._ult_status_log = text
            log("estado", text)
        self.lbl_status.setText(text)
        try:
            self.btn_consola.setToolTip(f"{text}\n\nMostrar / ocultar la consola (todo lo que hace el programa, con hora)")
        except Exception:
            pass

    def _on_reset_deck(self, deck):
        """Callback que dispara el Deck al hacer reset. Redibuja el timeline
        y muestra un mensaje de status para dar feedback visual."""
        try:
            self.timeline.redraw()
        except Exception:
            pass
        if deck is self.deck_a: self._target_rate_a = None
        else: self._target_rate_b = None
        self._set_status(f"⟲ Deck {deck.name}: tempo al 100 %")

    def _update_zoom(self):
        v = self.sld_zoom.value()
        self.lbl_zoom.setText(str(v))
        self.timeline.set_zoom_px(float(v))

    def _fin_ajuste(self, deck, completo):
        ep = self.__dict__.setdefault("_aj_ep", {})
        if ep.pop(id(deck), None) is not None:
            if completo:
                deck.mostrar_ajuste(1.0)
            deck.ocultar_ajuste()

    def _on_manito_release(self, deck):
        anchor = self.timeline.anchor_a if deck == "A" else self.timeline.anchor_b
        d = self.deck_a if deck == "A" else self.deck_b
        d.pos_manual = True                          # lo ubicó el usuario: si es el que entra, la mezcla arranca desde ahí
        self._set_status(f"✋ Deck {deck} movido a {fmt_time(anchor)}")

    def _plegar_al_inicio(self):
        """Al abrir el programa las carpetas arrancan contraídas (una sola vez)."""
        if getattr(self, "_plegado_inicial", False) or not self.biblioteca.agrupado:
            return
        if not self.biblioteca.count() or not self.biblioteca._es_cab(self.biblioteca.item(0)):
            return
        self._plegado_inicial = True
        self.biblioteca.plegar_todas()
        self.btn_plegar.setText(">>")
        self.btn_plegar.setToolTip("Expandir todas las carpetas")

    def _compactar_analisis_viejos(self):
        """Una sola vez por sesión, en segundo plano y despacio: pasa los análisis viejos (sin comprimir) al almacén nuevo."""
        if getattr(self, "_compactando", False):
            return
        self._compactando = True
        rutas = list(self.biblioteca.todas())
        self._cancelar_compactar = False
        def _trabajo():
            try:
                from setup import hilos_analisis as _ha
                almacen.compactar_legado(rutas, cancelar=lambda: self._cancelar_compactar, en_pausa=lambda: bool(_ha._SONANDO))
            except Exception:
                import traceback; log("programa", "compactar análisis: " + traceback.format_exc())
        threading.Thread(target=_trabajo, daemon=True, name="compactar-analisis").start()

    def _post_carga_biblioteca(self):
        """La lista ya está completa y con BPM / tono: si está en vista por carpetas se ordena como siempre."""
        self._compactar_analisis_viejos()
        try:
            if self.biblioteca.agrupado:
                self._ordenar_lista()
                self._plegar_al_inicio()
        except Exception:
            import traceback; traceback.print_exc()

    def closeEvent(self, event):
        log("programa", "cerrando AutoMix")
        try: self.f2_global.quitar()
        except Exception: pass
        try: self.vol_global.quitar()
        except Exception: pass
        try: self.flechas_slider.quitar()
        except Exception: pass
        self._cancelar_compactar = True                  # el compactador se detiene (cada archivo se escribe completo o no se escribe)
        try:
            if self.mixer.grabador.activo:
                self._parar_grabacion()          # que el .wav quede completo
        except Exception:
            pass
        try:
            if not (self.biblioteca._cargando_lib and not (self.deck_a.path or self.deck_b.path)):     # (si se cierra a mitad de la carga, no se pisan los decks guardados)
                guardar_config(decks_cargados={"A": self.deck_a.path, "B": self.deck_b.path})     # al abrir vuelven a quedar cargados
        except Exception:
            pass
        try: CONSOLA.ocultar()
        except Exception: pass
        _cerrar_pool()
        self.biblioteca.guardar()
        self.mix_timer.stop()
        self.mix_pausa_timer.stop()
        self._smooth_timer.stop()
        self.magnet_timer.stop()
        try: self.mixer.aur.cerrar()
        except Exception: pass
        self.mixer.stop()
        super().closeEvent(event)


def _mensajes_qt(tipo, ctx, msg):
    log("qt", msg)




def _avisar_sin_audio(app, splash, error):
    """Muestra un aviso claro cuando no se puede abrir la salida de audio (en vez de cerrarse con un traceback)."""
    from PySide6.QtWidgets import QMessageBox
    try:
        n_salidas = sum(1 for d in sd.query_devices() if d["max_output_channels"] > 0)
    except Exception:
        n_salidas = 0
    log("audio", f"no se pudo abrir la salida de audio ({error}) · salidas detectadas: {n_salidas}")
    if splash is not None:
        try: splash.close()
        except Exception: pass
    if n_salidas == 0:
        titulo = "AutoMix · Sin salida de audio"
        texto = ("Esta PC no tiene ningún dispositivo de audio de salida disponible.\n\n"
                 "Qué revisar:\n"
                 "• Que haya placa de sonido y su driver instalado (Administrador de dispositivos).\n"
                 "• Que conectaste parlantes, auriculares o un monitor con audio HDMI.\n"
                 "• Que el servicio «Audio de Windows» esté en ejecución.\n"
                 "• En Sonido > Reproducción, que el dispositivo no esté deshabilitado.\n\n"
                 "Cuando tengas una salida de audio activa, volvé a abrir AutoMix.")
    else:
        titulo = "AutoMix · No se pudo abrir el audio"
        texto = (f"Hay {n_salidas} salida(s) de audio, pero no se pudo abrir la predeterminada.\n\n"
                 "Probá elegir otra salida predeterminada en Windows (Sonido > Reproducción), "
                 "cerrar otros programas que usen el audio en modo exclusivo y volver a abrir AutoMix.")
    cuadro = QMessageBox(QMessageBox.Icon.Critical, titulo, texto)
    cuadro.setDetailedText(str(error))
    cuadro.exec()


def main(tiempos=None, splash=None):
    t_ini = time.perf_counter()
    sys.setswitchinterval(0.0005)        # el hilo del audio no espera 5 ms a que la pantalla suelte Python (menos trancadas)
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        from setup.icono import aplicar_icono
        aplicar_icono(app)
    except Exception:
        pass
    try:
        from PySide6.QtCore import qInstallMessageHandler, __version__ as _qv
        qInstallMessageHandler(_mensajes_qt)             # los avisos internos de Qt también van a la consola
        import platform
        log("programa", f"AutoMix · Python {platform.python_version()} · PySide6 {_qv} · numpy {np.__version__} · {platform.platform()}")
        try:
            dev = sd.query_devices(sd.default.device[1] if sd.default.device[1] is not None and sd.default.device[1] >= 0 else None, "output")
            log("audio", f"salida: {dev['name']} · {int(dev['default_samplerate'])} Hz · {dev['max_output_channels']} canales")
        except Exception as e:
            log("audio", f"no se pudo leer el dispositivo de salida ({e})")
    except Exception:
        pass
    t_v0 = time.perf_counter()
    try:
        from setup.arranque import mensaje
        mensaje(splash, "Armando la ventana…")
    except Exception:
        mensaje = None
    try:
        w = AutoMix()
    except sd.PortAudioError as e:
        _avisar_sin_audio(app, splash, e)
        sys.exit(1)
    w._ajustar_escala_pantalla()          # se repite con la ventana ya armada (todavía sin mostrar): ahí la medida es la real
    t_v1 = time.perf_counter()
    w.show()
    if splash is not None:
        try: splash.finish(w)
        except Exception: pass
    t_v2 = time.perf_counter()
    if tiempos:
        t0, t1, t2 = tiempos
        log("arranque", f"dependencias {t1 - t0:.2f} s · módulos {t2 - t1:.2f} s · Qt+audio {t_v0 - t_ini:.2f} s · armar ventana {t_v1 - t_v0:.2f} s · mostrar {t_v2 - t_v1:.2f} s · TOTAL {t_v2 - t0:.2f} s")
    log("programa", f"listo · biblioteca {w.biblioteca.count()} tema(s) · duración de mezcla {w.spin_mix.value()} s")
    sys.exit(app.exec())
