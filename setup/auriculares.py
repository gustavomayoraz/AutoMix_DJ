"""AutoMix — salida de auriculares (PFL): escuchar un deck por otra placa de sonido sin mandarlo a los parlantes."""
import threading
from collections import deque
import numpy as np
import sounddevice as sd
from setup.ajustes import NIVEL_V2


class _Previa:
    """Reproducción propia de los auriculares para un deck: tiene su PROPIA posición y no toca al deck (no hace play,
    no dispara la mezcla automática ni suena en los parlantes)."""
    def __init__(self):
        self.audio = None
        self.pos = 0.0               # en muestras
        self.playing = False


class Auriculares:
    """Segunda salida de audio (por ejemplo un DAC USB). Recibe, bloque a bloque, el audio de los decks marcados
    (antes del crossfader) y lo reproduce por su propio dispositivo. El alimentado usa buffers PRE-ALOCADOS y `out=`:
    no aloca en el callback de audio principal."""
    PREVIO_MIN = 4        # bloques que se juntan antes de empezar a sonar (evita cortes por llegar justo)
    PREVIO_MAX = 14       # si se cortan seguido (Bluetooth entrega a ráfagas), el colchón crece solo hasta acá
    MAX_EXTRA = 6         # cuántos bloques de más se aguantan antes de descartar los más viejos
    POOL = 20             # buffers pre-alocados (20 × 8 KB ≈ 160 KB)

    def __init__(self, sr, bloque=1024):
        self.sr = sr
        self.bloque = bloque
        self.stream = None
        self.nombre = ""
        self.error = ""
        self.canales = 2
        self.PREVIO = self.PREVIO_MIN
        self.MAX_BLOQUES = self.PREVIO + self.MAX_EXTRA
        self.cortes = 0
        self.GANANCIA = 3.5
        self.vol = 0.8
        self.cue_a = False
        self.cue_b = False
        self.decks = {}
        self.prev = {"A": _Previa(), "B": _Previa()}
        self._cola = deque()
        self._lock = threading.Lock()
        self._sonando = False
        # ---- Sin allocations en `alimentar`: buffer scratch + pool ----
        self._scratch_cue = np.empty((bloque, 2), dtype=np.float32)
        self._pool = [np.empty((bloque, 2), dtype=np.float32) for _ in range(self.POOL)]

    def _tomar_buffer(self, frames):
        if self._pool:
            buf = self._pool.pop()
            if buf.shape[0] == frames:
                return buf
        return np.empty((frames, 2), dtype=np.float32)

    def _devolver_buffer(self, buf):
        if len(self._pool) < self.POOL and buf.shape == (self.bloque, 2):
            self._pool.append(buf)

    # ---- dispositivos -------------------------------------------------
    @staticmethod
    def listar(excluir=None):
        res = []
        try:
            try:
                apis = sd.query_hostapis()
            except Exception:
                apis = []
            try:
                api_def = sd.default.hostapi
            except Exception:
                api_def = None
            for i, d in enumerate(sd.query_devices()):
                ch = int(d.get("max_output_channels", 0))
                if ch < 1:
                    continue
                ha = d.get("hostapi")
                api = apis[ha]["name"] if apis and ha is not None and 0 <= ha < len(apis) else ""
                if "WDM" in api or "ASIO" in api:
                    continue
                vis = d["name"] if (api_def is None or ha == api_def) else f"{d['name']} [{api}]"
                if ch == 1:
                    vis += " · mono"
                res.append((i, vis, ch, api, ha == api_def or api_def is None))
        except Exception:
            pass
        res.sort(key=lambda x: (not x[4], x[0]))
        def norm(t):
            return " ".join(t.lower().split())[:28]
        ex = norm(excluir) if excluir else None
        vistos, fin = set(), []
        for i, v, ch, api, _ in res:
            base = norm(v.split(" [")[0].replace(" · mono", ""))
            bajo = base
            if any(k in bajo for k in ("primario", "primary sound", "sound mapper", "asignador", "mapeador")):
                continue
            if ex and (base == ex or base.startswith(ex[:20]) and ex.startswith(base[:20])):
                continue
            clave = (base, ch == 1)
            if clave in vistos:
                continue
            vistos.add(clave)
            fin.append((i, v, ch, api))
        return fin

    _pres_cache = None            # última lista leída por el hilo vigía (la consulta a Windows puede tardar varios cientos de ms)
    _pres_hilo = None

    @staticmethod
    def nombres_presentes():
        """Nombres de las salidas que ve Windows. NO consulta a Windows en el hilo de la ventana (en algunos equipos tarda de
        0,3 a más de 1 s y trababa la interfaz y el audio): lo lee un hilo aparte cada segundo y esto devuelve lo último leído."""
        cls = Auriculares
        if cls._pres_hilo is None:
            cls._pres_cache = cls._leer_presentes()          # primera vez: una lectura directa para tener un dato inicial
            def _vigia():
                import time as _t
                while True:
                    _t.sleep(1.0)
                    try:
                        cls._pres_cache = cls._leer_presentes()
                    except Exception:
                        pass
            cls._pres_hilo = threading.Thread(target=_vigia, daemon=True, name="vigia-dispositivos")
            cls._pres_hilo.start()
        return cls._pres_cache

    @staticmethod
    def _leer_presentes():
        try:
            import os, ctypes
            if os.name != "nt":
                return None
            from ctypes import wintypes as w

            class CAPS(ctypes.Structure):
                _fields_ = [("mid", w.WORD), ("pid", w.WORD), ("ver", w.UINT), ("nombre", ctypes.c_wchar * 32),
                            ("formatos", w.DWORD), ("canales", w.WORD), ("r", w.WORD), ("soporte", w.DWORD)]
            winmm = ctypes.windll.winmm
            res = []
            for i in range(winmm.waveOutGetNumDevs()):
                c = CAPS()
                if winmm.waveOutGetDevCapsW(ctypes.c_size_t(i), ctypes.byref(c), ctypes.sizeof(c)) == 0:
                    res.append(" ".join(c.nombre.lower().split()))
            return res or None
        except Exception:
            return None

    def sigue_presente(self):
        if self.stream is None or not self.nombre:
            return True
        pres = self.nombres_presentes()
        if pres is None:
            return True
        return any(self.coincide(self.nombre, p) for p in pres)

    @staticmethod
    def coincide(nombre, presente):
        base = " ".join(nombre.split(" [")[0].replace(" · mono", "").lower().split())
        p = presente
        return len(p) >= 6 and (p in base or base in p or base[:28] == p[:28])

    @property
    def activo(self):
        return self.stream is not None

    def abrir(self, indice, nombre="", canales=2, api=""):
        self.cerrar()
        self.PREVIO = self.PREVIO_MIN; self.MAX_BLOQUES = self.PREVIO + self.MAX_EXTRA; self.cortes = 0
        try:
            extra = None
            if "WASAPI" in api:
                try:
                    extra = sd.WasapiSettings(auto_convert=True)
                except Exception:
                    extra = None
            self.canales = 1 if canales < 2 else 2
            self.stream = sd.OutputStream(samplerate=self.sr, channels=self.canales, dtype="float32", device=indice,
                                          callback=self._cb, blocksize=self.bloque, latency=0.15, extra_settings=extra)
            self.stream.start()
            self.nombre = nombre
            self.error = ""
            return True
        except Exception as e:
            self.stream = None
            self.nombre = ""
            self.error = str(e)
            return False

    def cerrar(self):
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop(); s.close()
            except Exception:
                pass
        with self._lock:
            self._cola.clear()
            self._sonando = False

    # ---- audio ----------------------------------------------------------
    def alimentar(self, a, b):
        """Lo llama el callback principal con el audio de cada deck ya renderizado (antes del crossfader).
        Usa el buffer `_scratch_cue` y `out=` para no alocar en el hilo de audio."""
        if self.stream is None:
            return
        ca, cb = self.cue_a, self.cue_b
        pa, pb = self.prev["A"].playing, self.prev["B"].playing
        if not (ca or cb or pa or pb):
            if self._cola:
                with self._lock:
                    self._cola.clear(); self._sonando = False
            return

        n = len(a)
        scratch = self._scratch_cue
        if scratch.shape[0] < n:
            scratch = np.empty((n, 2), dtype=np.float32)
            self._scratch_cue = scratch
        scratch[:n].fill(0.0)

        any_added = False
        for nom, vivo, flag in (("A", a, ca), ("B", b, cb)):
            if self.prev[nom].playing:
                x = self._render_previa(nom, n)
            else:
                x = vivo[:n] if flag else None
            if x is not None:
                np.add(scratch[:n], x, out=scratch[:n])
                any_added = True
        if not any_added:
            return

        # saturación suave en el mismo buffer (out=): cero alloc
        g = float(self.vol) ** 2
        np.multiply(scratch[:n], g * self.GANANCIA, out=scratch[:n])
        np.tanh(scratch[:n], out=scratch[:n])

        # copia al buffer del pool para encolar (la cola no puede tener referencias al scratch)
        buf = self._tomar_buffer(n)
        np.copyto(buf[:n], scratch[:n])
        with self._lock:
            self._cola.append(buf)
            while len(self._cola) > self.MAX_BLOQUES:
                old = self._cola.popleft()
                self._devolver_buffer(old)

    # ---- previa propia (play de auriculares) ----------------------------
    def previa_play(self, nom):
        d = self.decks.get(nom)
        p = self.prev[nom]
        if d is None or d.audio is None:
            return False
        if p.audio is not d.audio:
            p.audio = d.audio
            p.pos = float(d.cursor)
        if p.pos >= len(p.audio) - 2048:
            p.pos = 0.0
        p.playing = True
        return True

    def previa_pausa(self, nom):
        self.prev[nom].playing = False

    def previa_ir(self, nom, fraccion):
        p = self.prev[nom]
        d = self.decks.get(nom)
        if d is None or d.audio is None:
            return
        if p.audio is not d.audio:
            p.audio = d.audio
        p.pos = float(max(0.0, min(0.999, fraccion)) * len(p.audio))

    def previa_fraccion(self, nom):
        p = self.prev[nom]
        d = self.decks.get(nom)
        if d is None or d.audio is None or p.audio is not d.audio:
            return 0.0
        return p.pos / max(1, len(p.audio))

    def _render_previa(self, nom, frames):
        p = self.prev[nom]
        d = self.decks.get(nom)
        au = d.audio if d is not None else None
        if au is None or au is not p.audio:
            p.playing = False
            return None
        rate = float(d.rate_eff)
        n = len(au)
        pos = p.pos + np.arange(frames, dtype=np.float64) * rate
        if pos[-1] + 2 >= n:
            p.playing = False
            ok = pos + 2 < n
            if not ok.any():
                return None
            pos = np.where(ok, pos, 0.0)
            valido = ok[:, None]
        else:
            valido = None
        i = pos.astype(np.int64)
        t = (pos - i).astype(np.float32)[:, None]
        y = au[i] * (1.0 - t) + au[i + 1] * t
        if valido is not None:
            y = y * valido
        else:
            p.pos += frames * rate
        return (y * (d.auto_gain * NIVEL_V2)).astype(np.float32)

    def _cb(self, outdata, frames, time_info, status):
        partes = []; falta = frames
        with self._lock:
            if not self._sonando:
                if len(self._cola) >= self.PREVIO:
                    self._sonando = True
            if not self._sonando:
                outdata[:] = 0
                return
            while falta > 0 and self._cola:
                blk = self._cola[0]
                if len(blk) <= falta:
                    partes.append(blk); self._cola.popleft(); falta -= len(blk)
                else:
                    partes.append(blk[:falta]); self._cola[0] = blk[falta:]; falta = 0
            if falta > 0:
                partes.append(np.zeros((falta, 2), dtype=np.float32))
                self._sonando = False
                self.cortes += 1
                if self.PREVIO < self.PREVIO_MAX:
                    self.PREVIO += 2
                    self.MAX_BLOQUES = self.PREVIO + self.MAX_EXTRA
        blk = np.concatenate(partes) if len(partes) > 1 else partes[0]
        if outdata.shape[1] == 1:
            outdata[:, 0] = blk.mean(axis=1)
        else:
            outdata[:] = blk
