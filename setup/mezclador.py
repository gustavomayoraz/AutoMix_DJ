"""AutoMix — mezclador global (callback de audio) y grabación de la mezcla."""
from pathlib import Path
import math
import threading
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
from setup.auriculares import Auriculares
from setup.eco import EcoSalida
from setup.efectos import EfectoSalida
from setup.dsp import AutoMejora, CruceLR4, LimitadorSuave, RealceGrave, TonoAgudos, TonoMedios


class GrabadorMezcla:
    """Graba a un .mp3 (320 kbps) o .wav (16 bits) exactamente lo que sale por los parlantes. El callback de audio solo copia el bloque
    a un buffer PRE-ALOCADO del pool y lo encola; un hilo aparte escribe en disco. NO aloca memoria en el callback."""
    POOL = 24                     # buffers pre-alocados (24 × 8 KB ≈ 192 KB)

    def __init__(self, sr=44100, blocksize=1024):
        import queue
        self.sr = sr
        self.activo = False
        self.ruta = None
        self.muestras = 0
        self.perdidos = 0
        self._blocksize = blocksize
        self._q = queue.Queue(maxsize=600)         # ~14 s de margen si el disco se atrasa
        self._hilo = None
        self._error = None
        self.terminando = False; self.listo = False
        self._rampa = None; self._fade_n = 0; self._cola_n = 0; self._fade_i = 0
        self._pool = [np.empty((blocksize, 2), dtype=np.float32) for _ in range(self.POOL)]

    def _nuevo_buffer(self):
        return self._pool.pop() if self._pool else np.empty((self._blocksize, 2), dtype=np.float32)

    def _devolver_buffer(self, buf):
        if len(self._pool) < self.POOL and buf.shape == (self._blocksize, 2):
            self._pool.append(buf)

    def _abrir_archivo(self, base, formato):
        """Abre el archivo de la grabación. MP3 = 320 kbps constantes (calidad máxima); WAV = 16 bits sin pérdida. Si el MP3 no se puede
        (libsndfile viejo) cae a WAV. Devuelve (SoundFile, ruta real)."""
        if str(formato).lower() == "mp3":
            ruta = base.with_suffix(".mp3")
            try:
                f = sf.SoundFile(str(ruta), mode="w", samplerate=self.sr, channels=2, format="MP3",
                                 compression_level=0.0, bitrate_mode="CONSTANT")
                self.formato_real = "mp3"
                return f, ruta
            except Exception as e:
                print(f"[grabación] no se puede grabar MP3 ({e}): se graba en WAV")
        ruta = base.with_suffix(".wav")
        self.formato_real = "wav"
        return sf.SoundFile(str(ruta), mode="w", samplerate=self.sr, channels=2, subtype="PCM_16"), ruta

    def iniciar(self, carpeta, formato="mp3"):
        carpeta = Path(carpeta); carpeta.mkdir(parents=True, exist_ok=True)
        base = carpeta / time.strftime("AutoMix_%Y-%m-%d_%H-%M-%S")
        self.muestras = 0; self.perdidos = 0; self._error = None
        self.terminando = False; self.listo = False; self._rampa = None
        while not self._q.empty():
            try: self._q.get_nowait()
            except Exception: break
        # pool limpio (todos los buffers vuelven)
        self._pool = [np.empty((self._blocksize, 2), dtype=np.float32) for _ in range(self.POOL)]
        archivo, self.ruta = self._abrir_archivo(base, formato)
        self._hilo = threading.Thread(target=self._escribir, args=(archivo,), daemon=True)
        self.activo = True
        self._hilo.start()
        return self.ruta

    def finalizar(self, fade_s=15.0, cola_s=3.0):
        """Cierre suave: lo que va al archivo baja a cero en `fade_s` segundos y después hay `cola_s` segundos de silencio.
        (Solo afecta a la grabación: lo que suena por los parlantes no cambia.) Cuando termina, `listo` pasa a True."""
        if not self.activo or self.terminando:
            return
        n = max(1, int(fade_s * self.sr))
        x = np.arange(n, dtype=np.float32) / n
        self._rampa = (0.5 * (1.0 + np.cos(np.pi * x))).astype(np.float32)      # coseno: arranque y llegada suaves
        self._fade_n = n; self._cola_n = int(cola_s * self.sr); self._fade_i = 0
        self.terminando = True

    @property
    def restante(self):
        """Segundos que faltan para que termine el cierre suave."""
        return max(0.0, (self._fade_n + self._cola_n - self._fade_i) / float(self.sr)) if self.terminando else 0.0

    def escribir(self, bloque):               # callback de audio: SIN allocations si hay buffer en el pool
        try:
            if self.listo:
                return
            buf = self._nuevo_buffer()
            np.copyto(buf, bloque)            # copia sin alocar
            if self.terminando:
                n = len(bloque); i = self._fade_i
                if i >= self._fade_n:
                    buf[:n] = 0.0
                else:
                    m = min(n, self._fade_n - i)
                    buf[:m] *= self._rampa[i:i + m, None]
                    if m < n:
                        buf[m:n] = 0.0
                self._fade_i = i + n
                if self._fade_i >= self._fade_n + self._cola_n:
                    self.listo = True
            self._q.put_nowait(buf)
            self.muestras += len(bloque)
        except Exception:
            self.perdidos += len(bloque)

    def _escribir(self, archivo):
        try:
            while True:
                b = self._q.get()
                if b is None:
                    break
                archivo.write(b)
                self._devolver_buffer(b)      # devuelve al pool: cero alloc en régimen
        except Exception as e:
            self._error = str(e)
            self.activo = False
        finally:
            try: archivo.close()
            except Exception: pass

    def detener(self):
        """Corta la grabación y espera a que el disco termine. Devuelve (ruta, segundos)."""
        self.activo = False
        if self._hilo is not None:
            self._q.put(None)
            self._hilo.join(timeout=10)
            self._hilo = None
        return self.ruta, self.muestras / float(self.sr)

    @property
    def segundos(self):
        return self.muestras / float(self.sr)


class GlobalMixer:
    F_CRUCE = 150.0           # por debajo = "graves" (bombo y bajo)
    ESPERA_COMPAS_S = 2.5     # (respaldo) espera máxima de la línea de compás si no se conoce el BPM

    # Errores posibles del callback (se muestran en la consola desde el thread de la UI)
    ERR = {1: "auriculares", 2: "mezclar", 3: "multibanda", 4: "medios", 5: "agudos",
           6: "golpe 43 Hz", 7: "efectos", 8: "limitador", 9: "recorte", 10: "eco de salida"}

    def __init__(self, deck_a, deck_b, sr=44100, blocksize=1024):
        self.deck_a = deck_a
        self.deck_b = deck_b
        self.sr = sr
        self.blocksize = blocksize
        self.stream = None
        self.snap_pos = None        # (pos A, pos B) en muestras, tomadas en el MISMO instante al final de cada bloque
        self.gain_obj = 1.0
        self._gain_act = 1.0
        self.mb = AutoMejora(sr)
        self.golpe = RealceGrave(sr)
        self.medios = TonoMedios(sr)
        self.agudos = TonoAgudos(sr)
        self.mb_activo = False
        self._mb_wet = 0.0
        self.banda_activa = False         # cruce por bandas (lo enciende la ventana)
        self._xo_a = CruceLR4(self.F_CRUCE, sr)
        self._xo_b = CruceLR4(self.F_CRUCE, sr)
        self._g = None                    # ganancias actuales: a_lo, a_hi, b_lo, b_hi
        self._swap_espera = 0.0
        self._cut = [1.0, 1.0]            # cierre de graves actual de cada deck (suavizado de bloque en bloque)
        self.limitador = LimitadorSuave(sr)
        self.comp_activo = True            # compensador de nivel (lo maneja la ventana)
        self._comp = 1.0                   # ganancia actual del compensador (1 = sin efecto)
        self._e = [0.0, 0.0, 0.0]          # energías suavizadas: deck A, deck B y la suma
        self.comp_db = 0.0
        self.vu = (0.0, 0.0, 1.0, 1.0)     # picos L, R (a la salida), ganancia del limitador y del compensador
        self.grabador = GrabadorMezcla(sr, blocksize)
        self.aur = Auriculares(sr, blocksize)      # salida de auriculares (PFL)
        self.fx = EfectoSalida(sr)                 # efectos de sonido (sirena, bocina...) que se suman a la mezcla
        self.eco = EcoSalida(sr, blocksize)        # filtro + eco con que se va el tema que sale cuando los dos no combinan (lo arma la ventana)
        self.cortes_audio = 0              # veces que la placa pidió audio y no llegó a tiempo
        self.cb_max_ms = 0.0               # lo más que tardó un bloque desde el último aviso
        self._cb_n = 0                     # bloques servidos (el vigilante mira que siga avanzando)
        self._stream_cayo = False          # PortAudio avisó que la salida terminó (p. ej. desenchufaron el DAC USB)
        self._recuperando = False
        self._vig = (0, 0.0)               # (último _cb_n visto, momento)
        self._aviso_salida = None          # texto para mostrar en la barra de estado (lo recoge la ventana)
        self._sin_salida_desde = None
        self.aur_perdida = False

        # ---- Buffers y rampas PRE-ALOCADOS (el callback nunca debe allocar) ----
        self._err_flag = 0                 # 0 = sin error; >0 = código de error (se resetea al leerlo)
        self._rms_n = 0                    # contador para calcular el RMS cada N bloques
        self._vu_n = 0                     # idem para el VU
        self._ramp01 = np.linspace(0.0, 1.0, blocksize, dtype=np.float32)   # rampa lineal fija
        self._ramp1d = np.empty(blocksize, dtype=np.float32)                # rampa temporal (se reusa)
        self._scratch_1mr = np.empty(blocksize, dtype=np.float32)           # 1 - ramp (para crossfade MB)
        self._scratch_mix = np.empty((blocksize, 2), dtype=np.float32)      # mix * (1-r)
        self._scratch_proc = np.empty((blocksize, 2), dtype=np.float32)     # proc * r

    @staticmethod
    def _db_muestras(deck):
        an = deck.analysis
        if not an:
            return None
        c = deck._db_cache
        if c is None or c[0] is not an:
            db = an.get("downbeats_full") or an.get("downbeats") or []
            arr = np.asarray(db, dtype=np.float64) * float(deck.sr)
            deck._db_cache = c = (an, arr)
        return c[1] if len(c[1]) else None

    def _offset_compas(self, deck, p0, frames):
        """Índice de muestra, dentro de este bloque, donde el deck que sale cruza una línea de compás.
        None = no cae en este bloque · -1 = no hay grilla (se traspasa ya)."""
        arr = self._db_muestras(deck)
        if arr is None:
            return -1
        adv = frames * max(1e-6, deck.rate_eff)
        i = int(np.searchsorted(arr, p0, side="left"))
        if i < len(arr) and arr[i] < p0 + adv:
            return int(max(0, min(frames - 1, (arr[i] - p0) / max(1e-6, deck.rate_eff))))
        return None

    @staticmethod
    def _vec_paso(g0, g1, k, r, frames):
        v = np.full(frames, g0, dtype=np.float32)
        k = max(0, min(k, frames - r))
        v[k:k + r] = np.linspace(g0, g1, r, endpoint=True, dtype=np.float32)
        v[k + r:] = g1
        return v[:, None]

    def _ganancias_bajos(self, tg, frames, pa, pb):
        g = self._g
        a0, b0, a1, b1 = g["a_lo"], g["b_lo"], tg["a_lo"], tg["b_lo"]
        if a0 == a1 and b0 == b1:
            self._swap_espera = 0.0
            return a1, b1
        sale = self.deck_a if a1 < a0 else (self.deck_b if b1 < b0 else None)
        if sale is None:                                   # los dos suben (p. ej. un deck se detuvo): rampa suave
            g["a_lo"], g["b_lo"] = a1, b1
            return self._rampa_2d(a0, a1, frames), self._rampa_2d(b0, b1, frames)
        p0 = pa if sale is self.deck_a else pb
        k = self._offset_compas(sale, p0, frames)
        if k is None:
            self._swap_espera += frames / float(self.sr)
            try:
                compas_s = 4.0 * 60.0 / (float(sale.analysis["bpm"]) * max(1e-6, sale.rate_eff))
            except Exception:
                compas_s = self.ESPERA_COMPAS_S
            if self._swap_espera < compas_s * 1.25:        # un compás entero (y un poco más) siempre contiene una línea
                return a0, b0                              # todavía no: se espera la próxima línea de compás
            k = 0
        self._swap_espera = 0.0
        r = max(8, min(int(0.020 * self.sr), frames))      # ~20 ms (1 ciclo de un bajo): sin clic ni 'escalón'
        k = 0 if k < 0 else max(0, k - r)                  # la rampa TERMINA en la línea de compás: el bombo nuevo cae entero
        g["a_lo"], g["b_lo"] = a1, b1                      # el traspaso queda hecho en este bloque
        return self._vec_paso(a0, a1, k, r, frames), self._vec_paso(b0, b1, k, r, frames)

    def _rampa_2d(self, g0, g1, frames):
        """Rampa con `frames` muestras, forma (frames,1), REUSANDO `_ramp1d` (sin allocar)."""
        if g0 == g1:
            return g1
        np.multiply(self._ramp01[:frames], g1 - g0, out=self._ramp1d[:frames])
        np.add(self._ramp1d[:frames], g0, out=self._ramp1d[:frames])
        return self._ramp1d[:frames, None]

    TAU_E = 0.35        # ventana de energía (s)
    TAU_ATK = 0.30      # cuánto tarda en bajar la suma cuando suenan los dos (s)
    TAU_REL = 0.90      # y en volver cuando uno se va (s)
    COMP_MIN = 0.56     # como máximo -5 dB

    def _compensar(self, p_a, p_b, mix, frames):
        """Mantiene el nivel de la suma parecido al del más fuerte de los dos: si suenan juntos (y a pleno)
        la mezcla no sube sola. Se compara la energía de cada deck con la de la suma, así no depende de un nivel fijo."""
        dt = frames / float(self.sr)
        k = 1.0 - math.exp(-dt / self.TAU_E)
        e = self._e
        e[0] += (float(np.mean(p_a * p_a)) - e[0]) * k
        e[1] += (float(np.mean(p_b * p_b)) - e[1]) * k
        e[2] += (float(np.mean(mix * mix)) - e[2]) * k
        obj = 1.0
        if self.comp_activo and e[2] > 1e-8:
            obj = max(self.COMP_MIN, min(1.0, math.sqrt(max(e[0], e[1]) / e[2])))
        c0 = self._comp
        c1 = c0 + (obj - c0) * (1.0 - math.exp(-dt / (self.TAU_ATK if obj < c0 else self.TAU_REL)))
        self._comp = c1
        self.comp_db = 20.0 * math.log10(max(1e-3, c1))
        if abs(c1 - 1.0) < 1e-4 and abs(c0 - 1.0) < 1e-4:
            return mix
        # multiplicate SIN allocar: ganancia lineal de c0 a c1 sobre la mezcla
        np.multiply(self._ramp01[:frames], c1 - c0, out=self._ramp1d[:frames])
        np.add(self._ramp1d[:frames], c0, out=self._ramp1d[:frames])
        np.multiply(mix, self._ramp1d[:frames, None], out=mix)
        return mix

    def _mezclar(self, a, b, frames, pa, pb):
        da, db = self.deck_a, self.deck_b
        tg = {"a_lo": da.vol_lo, "a_hi": da.vol_hi, "b_lo": db.vol_lo, "b_hi": db.vol_hi}
        if self._g is None:
            self._g = dict(tg)
        g = self._g
        if self.banda_activa:
            alo, ahi = self._xo_a.split(a)
            blo, bhi = self._xo_b.split(b)
            for i, (d, lo) in enumerate(((da, alo), (db, blo))):          # cierre de graves del Auto (AUTO_HPF): rampa dentro del bloque, sin clics
                c0 = self._cut[i]; obj = getattr(d, "cut_lo", 1.0)
                c1 = obj if abs(obj - c0) < 1e-3 else c0 + (obj - c0) * 0.25
                self._cut[i] = c1
                if c0 != 1.0 or c1 != 1.0:
                    lo *= self._rampa_2d(c0, c1, frames)
            ga_lo, gb_lo = self._ganancias_bajos(tg, frames, pa, pb)
            p_a = alo * ga_lo + ahi * self._rampa_2d(g["a_hi"], tg["a_hi"], frames)
            p_b = blo * gb_lo + bhi * self._rampa_2d(g["b_hi"], tg["b_hi"], frames)
        else:
            p_a = a * self._rampa_2d(g["a_hi"], tg["a_hi"], frames)
            p_b = b * self._rampa_2d(g["b_hi"], tg["b_hi"], frames)
            g["a_lo"], g["b_lo"] = tg["a_hi"], tg["b_hi"]
        g["a_hi"], g["b_hi"] = tg["a_hi"], tg["b_hi"]
        cola = None
        if self.eco.activo:
            try:
                p_a, p_b, cola = self.eco.aplicar(p_a, p_b, da.volume, db.volume, frames)
            except Exception:
                self.eco.activo = False
                if self._err_flag == 0: self._err_flag = 10
        mix = p_a + p_b
        try:
            mix = self._compensar(p_a, p_b, mix, frames)
        except Exception:
            self._comp = 1.0
        if cola is not None:
            mix = mix + cola
        return mix

    salida_pref = None                 # nombre (como lo muestra la lista) de la salida principal elegida; None = la predeterminada de Windows

    def indice_salida(self, nombre):
        """Índice de PortAudio de la salida estéreo con ese nombre (None si no hay nombre o no está conectada)."""
        if not nombre:
            return None
        try:
            for i, v, ch, api in Auriculares.listar():
                if v == nombre and ch >= 2:
                    return i
        except Exception:
            pass
        return None

    def iniciar(self, nombre=None):
        """Abre la salida principal al arrancar: la elegida si sigue conectada, si no la predeterminada."""
        idx = self.indice_salida(nombre)
        try:
            self.start(idx)
            self.salida_pref = nombre if idx is not None else None
        except Exception:
            self.stream = None
            self.salida_pref = None
            self.start(None)

    def cambiar_salida(self, nombre):
        """Pasa la salida principal a otro dispositivo (None = la predeterminada). Corta el sonido un instante.
        Devuelve True si quedó andando; si falla, vuelve a la que había."""
        idx = self.indice_salida(nombre)
        if nombre and idx is None:
            return False
        previo = self.salida_pref
        self.stop()
        try:
            self.start(idx)
        except Exception as e:
            print(f"[audio] no se pudo abrir la salida principal «{nombre}» ({e})")
            self.stream = None
            for otro in (self.indice_salida(previo), None):
                try:
                    self.start(otro)
                    break
                except Exception:
                    self.stream = None
            return False
        self.salida_pref = nombre or None
        return True

    def start(self, device=None):
        if self.stream is not None:
            return
        self.stream = sd.OutputStream(
            samplerate=self.sr, channels=2, dtype="float32",
            callback=self._cb, blocksize=self.blocksize, latency=0.25, device=device,      # 0.25 s de colchón: aguanta trancadas de ~200 ms (cargar un tema) sin el 'rrrr'
            finished_callback=self._al_terminar
        )
        self._stream_cayo = False
        self._vig = (self._cb_n, time.monotonic())
        self.stream.start()

    def _al_terminar(self):
        """Lo llama PortAudio cuando la salida se detiene. Solo marca; el trabajo lo hace el vigilante."""
        self._stream_cayo = True

    def nombre_salida(self):
        try:
            return sd.query_devices(self.stream.device)["name"]
        except Exception:
            try:
                return sd.query_devices(kind="output")["name"]
            except Exception:
                return "la salida predeterminada"

    def vigilar_salida(self):
        """Se llama desde el timer de la ventana. Si la salida principal murió (se desenchufó el DAC USB) o la de
        auriculares quedó muerta, arranca la recuperación en otro hilo para no trancar la interfaz."""
        s = self.stream
        if self._recuperando:
            return
        a = self.aur.stream
        if a is not None:
            try:
                muerto = not a.active
            except Exception:
                muerto = True
            ahora = time.monotonic()
            if not muerto and ahora - getattr(self, "_t_pres", 0) >= 1.0:
                self._t_pres = ahora
                self._no_pres = 0 if self.aur.sigue_presente() else getattr(self, "_no_pres", 0) + 1
                muerto = self._no_pres >= 2
            if muerto:
                self._no_pres = 0
                try: self.aur.cerrar()
                except Exception: pass
                for p in self.aur.prev.values():
                    p.playing = False
                self.aur_perdida = True
                self._aviso_salida = "🎧 Se desconectaron los auriculares: la previa se detuvo"
        if s is None and self._sin_salida_desde is None:
            return
        ahora = time.monotonic()
        n, t = self._vig
        if self._cb_n != n:
            self._vig = (self._cb_n, ahora)
            t = ahora                      # el audio avanzó desde la última vez: si la ventana se trabó un rato no es culpa de la salida (antes esto la reiniciaba y cortaba el sonido)
        muerto = self._stream_cayo or self._sin_salida_desde is not None
        if not muerto and s is not None:
            try:
                muerto = not s.active
            except Exception:
                muerto = True
            if not muerto and ahora - t > 1.5:
                muerto = True
        if muerto:
            self._recuperando = True
            threading.Thread(target=self._recuperar, daemon=True).start()

    def _recuperar(self):
        """Cierra lo que quedó colgado, reinicia PortAudio y abre la salida predeterminada actual."""
        try:
            if self._sin_salida_desde is None:
                self._sin_salida_desde = time.monotonic()
                self._aviso_salida = "⚠ Se desconectó la salida de audio: buscando otra…"
            s, self.stream = self.stream, None
            if s is not None:
                try: s.abort()
                except Exception: pass
                try: s.close()
                except Exception: pass
            try: self.aur.cerrar()
            except Exception: pass
            try:
                sd._terminate(); sd._initialize()
            except Exception as e:
                print(f"[audio] no se pudo reiniciar PortAudio ({e})")
            try:
                try:
                    self.start(self.indice_salida(self.salida_pref))         # la salida elegida si volvió; si no, la predeterminada
                except Exception:
                    self.stream = None
                    self.start(None)
            except Exception as e:
                self.stream = None
                print(f"[audio] sin salida disponible todavía ({e})")
                time.sleep(1.0)
                return
            self._sin_salida_desde = None
            self._aviso_salida = f"🔊 La salida de audio se desconectó: ahora suena por {self.nombre_salida()}"
        finally:
            self._recuperando = False

    def reiniciar_audio(self):
        """PortAudio solo lista los dispositivos al iniciar: los que se enchufaron o conectaron (Bluetooth) después no
        aparecen. Esto cierra todo, vuelve a leer los dispositivos y reabre la salida principal en el MISMO dispositivo
        de antes (aunque Windows haya cambiado el predeterminado)."""
        nombre = api = None
        try:
            if self.stream is not None:
                d = sd.query_devices(self.stream.device)
                nombre = d["name"]; api = sd.query_hostapis(d["hostapi"])["name"]
        except Exception:
            pass
        try: self.aur.cerrar()
        except Exception: pass
        self.stop()
        try:
            sd._terminate(); sd._initialize()
        except Exception as e:
            print(f"[audio] no se pudo reiniciar PortAudio ({e})")
        idx = None
        try:
            if nombre:
                for i, d in enumerate(sd.query_devices()):
                    if d["name"] == nombre and d["max_output_channels"] >= 2 and sd.query_hostapis(d["hostapi"])["name"] == api:
                        idx = i; break
        except Exception:
            pass
        try:
            self.start(idx)
        except Exception:
            self.start(None)

    def set_volumen(self, v01):
        self.gain_obj = float(max(0.0, min(1.0, v01))) ** 2

    def stop(self):
        s, self.stream = self.stream, None
        if s:
            try: s.stop()
            except Exception: pass
            try: s.close()
            except Exception: pass

    # ------------------------------------------------------------------
    # CALLBACK DE AUDIO — sin allocaciones en régimen (Fase 1)
    # Reglas: NADA de print/traceback, NADA de np.linspace/np.zeros/np.array de tamaño variable,
    # usar out= donde se pueda, VU/RMS cada N bloques, errores por bandera.
    # ------------------------------------------------------------------
    def _cb(self, outdata, frames, time_info, status):
        self._cb_n += 1
        if status and getattr(status, "output_underflow", False):
            self.cortes_audio += 1
        t_cb0 = time.perf_counter()

        da, db = self.deck_a, self.deck_b
        pa = da.cursor + da._cursor_frac + da.loop_desfase
        pb = db.cursor + db._cursor_frac + db.loop_desfase
        a = da.render(frames)
        b = db.render(frames)
        self.snap_pos = (da.cursor + da._cursor_frac + da.loop_desfase,
                         db.cursor + db._cursor_frac + db.loop_desfase)

        try:
            self.aur.alimentar(a, b)
        except Exception:
            if self._err_flag == 0: self._err_flag = 1

        try:
            mix = self._mezclar(a, b, frames, pa, pb)
        except Exception:
            if self._err_flag == 0: self._err_flag = 2
            outdata.fill(0)
            return

        # Multibanda con fundido cruzado a/desde el original
        w1 = 1.0 if self.mb_activo else 0.0
        if w1 > 0.0 or self._mb_wet > 0.0:
            try:
                proc = self.mb.procesar(mix)
                w0 = self._mb_wet
                if w0 != w1:
                    # ramp = ramp01 * (w1-w0) + w0
                    np.multiply(self._ramp01[:frames], w1 - w0, out=self._ramp1d[:frames])
                    np.add(self._ramp1d[:frames], w0, out=self._ramp1d[:frames])
                    # 1 - ramp
                    np.subtract(1.0, self._ramp1d[:frames], out=self._scratch_1mr[:frames])
                    # mix = mix*(1-r) + proc*r
                    np.multiply(mix, self._scratch_1mr[:frames, None], out=self._scratch_mix[:frames])
                    np.multiply(proc, self._ramp1d[:frames, None], out=self._scratch_proc[:frames])
                    np.add(self._scratch_mix[:frames], self._scratch_proc[:frames], out=mix)
                    self._mb_wet = w1
                else:
                    mix = proc
                if w1 == 0.0:
                    self.mb.reset()
            except Exception:
                self.mb_activo = False
                self._mb_wet = 0.0
                if self._err_flag == 0: self._err_flag = 3

        try:
            mix = self.medios.procesar(mix)
        except Exception:
            self.medios.db = 0.0
            if self._err_flag == 0: self._err_flag = 4
        try:
            mix = self.agudos.procesar(mix)
        except Exception:
            self.agudos.db = 0.0
            if self._err_flag == 0: self._err_flag = 5
        try:
            mix = self.golpe.procesar(mix)
        except Exception:
            self.golpe.cantidad = 0.0
            if self._err_flag == 0: self._err_flag = 6

        if self.fx.buf is not None:
            try:
                mix = self.fx.sumar(mix)
            except Exception:
                self.fx.detener()
                if self._err_flag == 0: self._err_flag = 7

        try:
            mix = self.limitador.procesar(mix)
        except Exception:
            if self._err_flag == 0: self._err_flag = 8

        # RMS cada N bloques (la UI no necesita más de ~4 Hz)
        self._rms_n += 1
        if self._rms_n >= 10:
            self._rms_n = 0
            try:
                v = mix.ravel()
                self.nivel_vis = float(np.sqrt(np.dot(v, v) / v.size))
            except Exception:
                self.nivel_vis = 0.0

        # Red de seguridad: peak sin allocar (min/max C-level)
        try:
            mn = float(mix.min()); mx = float(mix.max())
            peak = mx if mx > -mn else -mn
        except Exception:
            peak = 0.0
        if peak > 0.98:
            try:
                if peak > 1.5:
                    np.tanh(mix, out=mix)
                else:
                    np.clip(mix, -0.99, 0.99, out=mix)
            except Exception:
                if self._err_flag == 0: self._err_flag = 9

        # Volumen general (con rampa si cambió, sin allocar)
        g0, g1 = self._gain_act, self.gain_obj
        if g0 != g1:
            np.multiply(self._ramp01[:frames], g1 - g0, out=self._ramp1d[:frames])
            np.add(self._ramp1d[:frames], g0, out=self._ramp1d[:frames])
            np.multiply(mix, self._ramp1d[:frames, None], out=mix)
            self._gain_act = g1
        elif g1 != 1.0:
            np.multiply(mix, g1, out=mix)

        # VU cada N bloques (33 ms del timer son ~3 bloques)
        self._vu_n += 1
        if self._vu_n >= 3:
            self._vu_n = 0
            try:
                ch_l = mix[:, 0]; ch_r = mix[:, 1]
                pl = float(ch_l.max()); nl = float(ch_l.min())
                pr = float(ch_r.max()); nr = float(ch_r.min())
                pl = pl if pl > -nl else -nl
                pr = pr if pr > -nr else -nr
                self.vu = (pl, pr, float(self.limitador.g), float(self._comp))
            except Exception:
                pass

        outdata[:] = mix
        dt_cb = (time.perf_counter() - t_cb0) * 1000.0
        if dt_cb > self.cb_max_ms:
            self.cb_max_ms = dt_cb
        if self.grabador.activo:
            self.grabador.escribir(outdata)
