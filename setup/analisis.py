"""AutoMix — análisis de audio: BPM, compases, bombos, clave, voz (sin interfaz)."""
from pathlib import Path
import json
import math
import os
import re
import sys
import threading
import time
from scipy.signal import butter, sosfilt
import librosa
import numpy as np
from setup.ajustes import TARGET_LUFS_RMS


def lowpass_kick(y, sr, cutoff=150.0, order=4):
    sos = butter(order, cutoff, btype="low", fs=sr, output="sos")
    return sosfilt(sos, y)




def refine_onsets_to_peak(onset_env, onsets_frames, hop, sr, win_frames=3):
    refined = []
    n = len(onset_env)
    for f in onsets_frames:
        a = max(0, f - win_frames)
        b = min(n, f + win_frames + 1)
        if b <= a:
            continue
        local = onset_env[a:b]
        peak_off = int(np.argmax(local))
        peak_frame = a + peak_off
        if 0 < peak_frame < n - 1:
            y1 = onset_env[peak_frame - 1]
            y2 = onset_env[peak_frame]
            y3 = onset_env[peak_frame + 1]
            denom = (y1 - 2 * y2 + y3)
            if abs(denom) > 1e-12:
                delta = 0.5 * (y1 - y3) / denom
                delta = float(np.clip(delta, -0.5, 0.5))
            else:
                delta = 0.0
        else:
            delta = 0.0
        t = (peak_frame + delta) * hop / sr
        refined.append(t)
    return np.array(refined, dtype=float)




def detect_kicks(y, sr):
    y_low = lowpass_kick(y, sr, cutoff=150.0, order=4)
    hop = 256
    env = librosa.onset.onset_strength(y=y_low, sr=sr, hop_length=hop)      # (con aggregate=np.median daba 0 bombos en todos los temas)
    onsets_frames = librosa.onset.onset_detect(
        onset_envelope=env, sr=sr, hop_length=hop,
        backtrack=False, units="frames",
        pre_max=3, post_max=3, pre_avg=3, post_avg=3, delta=0.15, wait=3
    )
    return refine_onsets_to_peak(env, onsets_frames, hop, sr)




def detect_beats(y, sr):
    hop = 512
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop, units="frames")
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    beats = refine_onsets_to_peak(env, beat_frames, hop, sr)
    return float(tempo) if np.isscalar(tempo) else float(tempo[0]), beats




def detect_downbeats(y, sr, beats):
    if len(beats) < 4:
        return beats
    y_low = lowpass_kick(y, sr, cutoff=150.0)
    win = int(0.06 * sr)
    energies = []
    for b in beats:
        c = int(b * sr)
        a = max(0, c - win)
        z = min(len(y_low), c + win)
        if z > a:
            energies.append(float(np.mean(np.abs(y_low[a:z]))))
        else:
            energies.append(0.0)
    energies = np.array(energies)
    best_off, best_score = 0, -1.0
    for off in range(4):
        idx = np.arange(off, len(beats), 4)
        if len(idx) == 0:
            continue
        score = float(energies[idx].mean())
        if score > best_score:
            best_score, best_off = score, off
    return beats[best_off::4]




_LOCK_ANALISIS = threading.Lock()     # librosa/numba no tolera dos análisis a la vez (cierra el programa)


_HILOS_VIVOS = set()                  # un QThread NO puede destruirse mientras corre (Qt aborta el programa)




def _mantener_vivo(hilo):
    _HILOS_VIVOS.add(hilo)
    hilo.finished.connect(lambda h=hilo: _HILOS_VIVOS.discard(h))




_POOL = None


_POOL_BLOQ_HASTA = 0.0         # si el proceso aparte falla o se cuelga, un rato se analiza en el programa mismo


TIMEOUT_ANALISIS = 150.0       # un tema normal tarda unos 5-10 s; pasado esto se lo da por colgado


_POOL_USOS = 0                 # el proceso aparte se renueva cada tanto (un hijo de larga vida puede ir engordando)


POOL_RENOVAR_CADA = 12


_CALENTADO = False             # ya hubo un análisis completo en este arranque (numba ya compiló/cargó sus funciones)




def _cerrar_pool():
    global _POOL
    p, _POOL = _POOL, None
    if p is None:
        return
    try:
        procs = list((getattr(p, "_processes", None) or {}).values())
    except Exception:
        procs = []
    try:
        p.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    for pr in procs:                     # shutdown no mata a un hijo colgado
        try:
            pr.kill()
        except Exception:
            pass




def _limpiar_cache_numba():
    """Si un proceso de análisis muere de golpe (fallo nativo dentro de numba/librosa, p. ej. 'access violation'), la caché de
    funciones compiladas de librosa puede quedar a medias. Se borra para que numba la recompile limpia (tarda una sola vez)."""
    n = 0
    try:
        base = Path(librosa.__file__).resolve().parent
        for patron in ("*.nbi", "*.nbc"):
            for f in base.rglob(patron):
                try:
                    f.unlink(); n += 1
                except Exception:
                    pass
    except Exception:
        pass
    print(f"[análisis] caché de numba limpiada ({n} archivos): se recompila en el próximo análisis")
    return n




def _bajar_prioridad():
    """Se ejecuta dentro de cada proceso que analiza la biblioteca: prioridad baja, así el audio y la ventana no se traban."""
    try:
        modo = os.environ.get("AUTOMIX_ANALISIS_MAX")
        if modo == "1":
            pass                                           # análisis masivo con la ventana de progreso: a máxima velocidad
        elif modo == "aparte" and os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32                     # programa aparte: "por debajo de lo normal" (sigue usando todo el procesador que nadie pida)
            k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)
        elif os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            # PROCESS_MODE_BACKGROUND_BEGIN: el proceso pasa a "segundo plano" de verdad (CPU, DISCO y memoria con prioridad mínima):
            # no le saca disco ni procesador a la música que suena ni a la ventana. Si no se puede, prioridad baja común.
            if not k.SetPriorityClass(k.GetCurrentProcess(), 0x00100000):
                k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)       # BELOW_NORMAL
        else:
            os.nice(10)
    except Exception:
        pass
    try:                                                   # un solo hilo de cálculo por proceso (ya hay un proceso por núcleo)
        from threadpoolctl import threadpool_limits
        threadpool_limits(1)
    except Exception:
        pass




_POOL_UNO = None
_LOCK_UNO = threading.Lock()


def _uno_en_proceso_aparte(path, beats):
    """El "1" del compás en SU PROPIO proceso (prioridad baja) y con su propio candado: así nunca hace esperar el
    análisis de un tema que se está cargando. Si ese proceso muere no se repite en el programa (lo tumbaría)."""
    global _POOL_UNO
    with _LOCK_UNO:
        try:
            if _POOL_UNO is None:
                from concurrent.futures.process import ProcessPoolExecutor      # (import directo: en 3.15 el paquete los carga 'perezosos' y no se pueden llamar)
                import multiprocessing
                ctx = multiprocessing.get_context("spawn")
                pw = Path(sys.executable).with_name("pythonw.exe")
                if os.name == "nt" and pw.exists():
                    ctx.set_executable(str(pw))
                _POOL_UNO = ProcessPoolExecutor(max_workers=1, mp_context=ctx, initializer=_bajar_prioridad)
            return _POOL_UNO.submit(_analizar_uno, (path, beats)).result(timeout=TIMEOUT_ANALISIS)
        except Exception as e:
            p, _POOL_UNO = _POOL_UNO, None
            try:
                procs = list((getattr(p, "_processes", None) or {}).values())
                p.shutdown(wait=False, cancel_futures=True)
                for pr in procs:
                    pr.kill()
            except Exception:
                pass
            raise RuntimeError(f"el cálculo del 1 falló ({type(e).__name__})")




def analizar_archivo(path, solo_clave=False, solo_uno=None):
    """El análisis (librosa/numba) retiene el GIL por momentos y trababa el audio ('rrrr').
    Se hace en un proceso aparte. Si se cuelga o no se puede crear, se hace acá como antes. PERO si el proceso aparte
    MUERE de golpe (fallo nativo), repetirlo en el programa mismo lo tumbaría: se reintenta una vez con un proceso limpio
    (y la caché de numba recompilada) y, si vuelve a morir, ese tema da error sin llevarse puesto al programa."""
    global _POOL, _POOL_BLOQ_HASTA, _POOL_USOS, _CALENTADO
    if solo_uno is not None:
        return _uno_en_proceso_aparte(path, solo_uno)
    with _LOCK_ANALISIS:
        if time.monotonic() >= _POOL_BLOQ_HASTA:
            from concurrent.futures._base import TimeoutError as _TO
            from concurrent.futures.process import BrokenProcessPool
            for intento in (1, 2):
                try:
                    if _POOL is None:
                        from concurrent.futures.process import ProcessPoolExecutor      # (import directo: en 3.15 el paquete los carga 'perezosos' y no se pueden llamar)
                        import multiprocessing
                        ctx = multiprocessing.get_context("spawn")
                        pw = Path(sys.executable).with_name("pythonw.exe")      # el hijo no abre ventana negra
                        if os.name == "nt" and pw.exists():
                            ctx.set_executable(str(pw))
                        _POOL = ProcessPoolExecutor(max_workers=1, mp_context=ctx)
                    if solo_uno is not None:
                        res = _POOL.submit(_analizar_uno, (path, solo_uno)).result(timeout=TIMEOUT_ANALISIS)
                    else:
                        res = _POOL.submit(_analizar_clave if solo_clave else _analizar_archivo, path).result(timeout=TIMEOUT_ANALISIS)
                    _POOL_USOS += 1
                    _CALENTADO = True
                    if _POOL_USOS >= POOL_RENOVAR_CADA:
                        _POOL_USOS = 0
                        _cerrar_pool()
                    return res
                except Exception as e:
                    _cerrar_pool()
                    _POOL_USOS = 0
                    if isinstance(e, BrokenProcessPool):
                        print(f"[análisis] el proceso de análisis se cerró de golpe (intento {intento}/2)")
                        _CALENTADO = False
                        _limpiar_cache_numba()
                        if intento == 1:
                            continue
                        raise RuntimeError("el proceso de análisis se cerró de golpe (BrokenProcessPool)")
                    if isinstance(e, (_TO, OSError)):
                        _POOL_BLOQ_HASTA = time.monotonic() + 600.0
                        print(f"[análisis] el proceso aparte falló ({type(e).__name__}); se analiza en el programa durante unos minutos")
                    break
        if solo_uno is not None:
            return _analizar_uno((path, solo_uno))
        return _analizar_clave(path) if solo_clave else _analizar_archivo(path)




def _pista_bpm(path):
    """BPM que ya trae el nombre del archivo, ej. '[82 BPM] Te conozco.mp3' (None si no tiene)."""
    m = re.search(r"\[\s*(\d{2,3}(?:[.,]\d+)?)\s*BPM\s*\]", os.path.basename(str(path)), re.I)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return v if 50.0 <= v <= 220.0 else None




def _bpm_de_grilla(beats):
    """BPM preciso = 60 / media de los intervalos entre beats (sin los atípicos)."""
    b = np.asarray(beats, dtype=np.float64)
    if len(b) < 16:
        return None
    iv = np.diff(b)
    m = float(np.median(iv))
    if m <= 0:
        return None
    k = iv[np.abs(iv - m) < 0.08 * m]
    if len(k) < 12:
        return None
    return 60.0 / float(np.mean(k))




def detect_beats_con_pista(y, sr, hint):
    hop = 512
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    _, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=hop,
                                        start_bpm=float(hint), tightness=100, units="frames")
    return refine_onsets_to_peak(env, frames, hop, sr)




BPM_MIN, BPM_MAX = 70.0, 150.0     # rango musical: fuera de él, librosa suele equivocarse por una octava (82 -> 164)




def _plegar_tempo(tempo, beats, y, sr):
    """Si el tempo cae fuera de [70, 150) es casi seguro un error de octava (el doble o la mitad).
    Se lleva al rango y se corrige también la grilla de beats (se queda con la mitad de los golpes, los
    de más energía grave, o se agregan los intermedios), para que los downbeats sigan siendo cada 4 golpes."""
    beats = np.asarray(beats, dtype=np.float64)
    tempo = float(tempo)
    for _ in range(2):
        if tempo >= BPM_MAX and len(beats) >= 8:
            y_low = lowpass_kick(y, sr, cutoff=150.0)
            win = int(0.05 * sr)

            def energia(bs):
                v = []
                for b in bs:
                    c = int(b * sr); a = max(0, c - win); z = min(len(y_low), c + win)
                    if z > a: v.append(float(np.mean(np.abs(y_low[a:z]))))
                return float(np.mean(v)) if v else 0.0
            fase = 0 if energia(beats[0::2]) >= energia(beats[1::2]) else 1
            beats = beats[fase::2]
            tempo /= 2.0
        elif 0 < tempo < BPM_MIN and len(beats) >= 8:
            medios = (beats[:-1] + beats[1:]) / 2.0
            beats = np.sort(np.concatenate([beats, medios]))
            tempo *= 2.0
    return tempo, beats





# ---------------------------------------------------------------------
# Grilla de pulso regular. El tracker de beats de librosa suele "perder" el pulso en tramos largos
# (se corre media pulsación o una entera, o deriva y se re-engancha de golpe) y los downbeats heredan el
# error: por eso las marcas del timeline quedaban irregulares y el sync no podía coincidir. Los temas de
# estudio tienen tempo fijo, así que se ajusta UNA línea de pulso para todo el tema y se usa tanto para
# dibujar como para sincronizar.
# ---------------------------------------------------------------------
def _linea_de_beats(beats):
    """Línea (intercepto, período) que mejor explica la mayoría de los beats detectados, o None."""
    b = np.asarray(beats, dtype=np.float64)
    if len(b) < 24:
        return None
    p0 = float(np.median(np.diff(b)))
    if not (0.25 < p0 < 1.5):
        return None
    phi = np.mod(b - b[0], p0)
    nb = max(8, int(p0 / 0.01))
    h, _ = np.histogram(phi, bins=nb, range=(0, p0))
    h = np.convolve(np.r_[h[-2:], h, h[:2]], np.ones(5), "same")[2:-2]
    icpt, slope = b[0] + (int(np.argmax(h)) + 0.5) * p0 / nb, p0
    for tol in (0.12, 0.08, 0.05, 0.04, 0.03):
        j = np.round((b - icpt) / slope)
        ok = np.abs(b - (icpt + slope * j)) < tol
        if ok.sum() < 12:
            return None
        slope, icpt = np.polyfit(j[ok], b[ok], 1)
    j = np.round((b - icpt) / slope)
    frac = float(np.mean(np.abs(b - (icpt + slope * j)) < 0.04))
    if frac < 0.5 or not (0.25 < slope < 1.5):
        return None
    return float(icpt), float(slope), frac




def _env_grave(y, sr, hop=256):
    from scipy.signal import butter as _bt, sosfilt as _sf
    yl = _sf(_bt(4, 150, "low", fs=sr, output="sos"), y)
    e1 = librosa.onset.onset_strength(y=yl, sr=sr, hop_length=hop)
    e2 = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    n = min(len(e1), len(e2))
    env = e1[:n] / (e1[:n].std() + 1e-9) + 0.5 * e2[:n] / (e2[:n].std() + 1e-9)
    env = np.maximum(env - np.median(env), 0.0)
    return env, np.arange(n) * hop / float(sr)




def _linea_de_audio(env, t, bpm0, rango=0.03, paso=0.0001):
    """Línea de pulso fija buscada directamente sobre los golpes del audio (peine de períodos)."""
    p0 = 60.0 / float(bpm0)
    mejor = (-1.0, None, None)
    scores = []
    for f in np.arange(1.0 - rango, 1.0 + rango + 1e-9, paso):
        p = p0 * f
        nb = max(16, int(round(p / 0.004)))
        bins = np.floor(np.mod(t, p) / p * nb).astype(int)
        h = np.bincount(bins, weights=env, minlength=nb)
        hs = np.convolve(np.r_[h[-2:], h, h[:2]], np.ones(3) / 3, "same")[2:-2]
        k = int(hs.argmax())
        sc = float(hs[k] / (len(env) / nb))
        scores.append(sc)
        if sc > mejor[0]:
            mejor = (sc, p, (k + 0.5) * p / nb)
    sc, p, ph = mejor
    med = float(np.median(scores)) if scores else 1.0
    return ph, p, sc / max(med, 1e-6)




def _grilla_final(res, linea):
    """Reemplaza beats/downbeats/bpm de `res` por la línea de pulso (icpt, período)."""
    icpt, slope = float(linea[0]), float(linea[1])
    dur = float(res["duration"])
    j0 = int(np.ceil((0.0 - icpt) / slope)); j1 = int(np.floor((dur - icpt) / slope))
    jj = np.arange(j0, j1 + 1)
    bt = icpt + slope * jj
    d_raw = np.asarray(res.get("downbeats_full") or [], dtype=np.float64)
    modo = 0
    if len(d_raw):
        jd = np.round((d_raw - icpt) / slope)
        sel = np.abs(d_raw - (icpt + slope * jd)) < 0.05
        pos = (jd[sel] if sel.sum() >= 4 else jd).astype(int) % 4
        modo = int(np.bincount(pos, minlength=4).argmax())
    db = bt[(jj % 4) == modo]
    def _ralo(v, tope):
        v = list(map(float, v))
        return v if len(v) <= tope else v[::len(v) // tope + 1]
    res["beats_full"] = list(map(float, bt)); res["beats"] = _ralo(bt, 400)
    res["downbeats_full"] = list(map(float, db)); res["downbeats"] = _ralo(db, 200)
    bpm_n = 60.0 / slope
    if abs(bpm_n - float(res["bpm"])) / float(res["bpm"]) < 0.03:
        res["bpm"] = float(bpm_n)
    res["grid"] = 1




BPM_CORREGIDO_MIN, BPM_CORREGIDO_MAX = 50.0, 220.0      # la corrección a mano nunca deja un BPM fuera de esto




def grilla_manual(res, factor=1.0, uno_en=None):
    """Corrección a mano de la grilla de un tema (modifica `res`).
    factor 2 / 0.5 = BPM doble / mitad (se conserva la fase) · uno_en = segundo donde cae el "1" de un compás."""
    bpm0 = float(res["bpm"])
    bt0 = np.asarray(res.get("beats_full") or [], dtype=np.float64)
    d0 = np.asarray(res.get("downbeats_full") or [], dtype=np.float64)
    if len(bt0) >= 2:
        slope = float(np.median(np.diff(bt0)))
        icpt = float(d0[0]) if len(d0) else float(bt0[0])         # se ancla en el primer "1": ×2 y ÷2 se pueden deshacer sin perder la fase
    else:
        slope = 60.0 / bpm0
        icpt = float(d0[0]) if len(d0) else 0.0
    if factor and factor != 1.0:
        base = float(res.setdefault("bpm_base", bpm0))             # el BPM que detectó el análisis (antes de ×2 / ÷2)
        slope = slope / float(factor)
        if not (base / 2.0 - 0.5 <= 60.0 / slope <= base * 2.0 + 0.5):
            raise ValueError(f"ya está en el límite: se detectó {base:.0f} BPM y solo se puede ir de {base / 2:.0f} a {base * 2:.0f}")
    if not (BPM_CORREGIDO_MIN <= 60.0 / slope <= BPM_CORREGIDO_MAX):
        raise ValueError(f"{60.0 / slope:.0f} BPM queda fuera del rango razonable ({BPM_CORREGIDO_MIN:.0f}-{BPM_CORREGIDO_MAX:.0f})")
    if uno_en is not None:
        icpt = float(uno_en)                    # este golpe pasa a ser un "1": la grilla se re-ancla acá
        modo_fijo = 0
    else:
        modo_fijo = None
    dur = float(res["duration"])
    if modo_fijo is not None:
        j0 = int(np.ceil((0.0 - icpt) / slope)); j1 = int(np.floor((dur - icpt) / slope))
        jj = np.arange(j0, j1 + 1)
        bt = icpt + slope * jj
        db = bt[(jj % 4) == 0]
    else:
        d_old = np.asarray(res.get("downbeats_full") or [], dtype=np.float64)
        ref = float(d_old[0]) if len(d_old) else icpt                 # se mantiene dónde estaba un "1"
        j0 = int(np.ceil((0.0 - icpt) / slope)); j1 = int(np.floor((dur - icpt) / slope))
        jj = np.arange(j0, j1 + 1)
        bt = icpt + slope * jj
        k_ref = int(np.round((ref - icpt) / slope))
        db = bt[((jj - k_ref) % 4) == 0]
    def _ralo(v, tope):
        v = list(map(float, v))
        return v if len(v) <= tope else v[::len(v) // tope + 1]
    res["beats_full"] = list(map(float, bt)); res["beats"] = _ralo(bt, 400)
    res["downbeats_full"] = list(map(float, db)); res["downbeats"] = _ralo(db, 200)
    res["bpm"] = float(60.0 / slope)
    res["grid"] = 1
    res["manual"] = True
    return res




def plegar_bpm(res):
    """Si el BPM de un tema es absurdo (fuera de 50-220), lo lleva por octavas (×2 / ÷2) al rango musical.
    Devuelve True si cambió algo. Cubre errores del análisis y datos dañados."""
    try:
        b = float(res.get("bpm") or 0.0)
        if b <= 0 or BPM_CORREGIDO_MIN <= b <= BPM_CORREGIDO_MAX:
            return False
        k = min(range(-12, 13), key=lambda k: abs(math.log(b * 2.0 ** k / 105.0)))
        if k == 0:
            return False
        grilla_manual(res, 2.0 ** k)
        res.pop("manual", None)                    # fue un arreglo automático, no una corrección del usuario
        return True
    except Exception:
        return False




def desfase_rejilla_bombos(res):
    """Cuánto está corrida la grilla respecto de los bombos (segundos, mediana). None si no hay datos suficientes
    o los bombos no son coherentes con la grilla (música sin bombo marcado)."""
    bt = np.asarray(res.get("beats_full") or [], dtype=np.float64)
    ks = np.asarray(res.get("kicks_full") or [], dtype=np.float64)
    if len(bt) < 16 or len(ks) < 16:
        return None
    per = float(np.median(np.diff(bt)))
    i = np.clip(np.searchsorted(bt, ks), 1, len(bt) - 1)
    cerca = np.where(np.abs(ks - bt[i - 1]) <= np.abs(ks - bt[i]), bt[i - 1], bt[i])
    d = ks - cerca
    d = d[np.abs(d) < 0.35 * per]
    if len(d) < 16:
        return None
    med = float(np.median(d))
    mad = float(np.median(np.abs(d - med)))
    if mad > 0.06 * per or len(d) < 0.3 * len(bt):       # bombos dispersos: no es una medida confiable
        return None
    return med




def _desfase_rejilla_downbeats(res):
    """Fallback cuando no hay bombos claros (acústica, clásica, ambient):
    mide dónde cae el MÁXIMO de energía RMS dentro de cada compás (downbeat a downbeat).
    Como los compases están más espaciados que los beats, la waveform sub-muestreada (2000 pts) alcanza
    para tener resolución ~2-3 muestras por bin."""
    try:
        db = np.asarray(res.get("downbeats_full") or res.get("downbeats") or [], dtype=np.float64)
        wf = res.get("waveform") or []
        dur = float(res.get("duration") or 0)
        if len(db) < 16 or len(wf) < 200 or dur <= 0:
            return None
        period = float(np.median(np.diff(db)))
        if not (0.5 < period < 10.0):              # compases entre 6 y 120 BPM: plausible
            return None
        n = len(wf)
        step = dur / n
        if period / step < 8:                       # muy pocos puntos por compás: no sirve
            return None
        if len(db) > 100:                           # no hace falta más de 100 compases
            db = db[::len(db) // 100]
        BINS = 32
        hist = np.zeros(BINS); counts = np.zeros(BINS)
        for b in db:
            for k in range(BINS):
                t = b + (k / BINS) * period
                j = int(t / step)
                if 0 <= j < n:
                    hist[k] += wf[j]; counts[k] += 1
        if counts.min() <= 0:
            return None
        hist /= counts
        if hist.max() <= 0:
            return None
        # suavizar circular y buscar el pico
        hist_s = np.convolve(np.r_[hist[-2:], hist, hist[:2]], np.ones(5) / 5, "same")[2:-2]
        peak = int(np.argmax(hist_s))
        offset = (peak / BINS) * period
        if offset > period / 2:                     # envolver: pico "tarde" = pico "temprano del próximo compás"
            offset -= period
        if abs(offset) > 0.08:                      # más de 80 ms → sospechoso, no confiar
            return None
        return float(offset)
    except Exception:
        return None




def desfase_rejilla_auto(res):
    """Desfase de la grilla probando VARIAS formas; devuelve la primera medición válida.
    1) Bombos (mediana): precisa si hay kick claro (electrónica, pop, rock).
    2) Downbeats + RMS: fallback para música sin kick (acústica, clásica, ambient).
    Devuelve segundos (positivo = grilla adelantada, hay que correrla hacia adelante) o None."""
    d = desfase_rejilla_bombos(res)
    if d is not None:
        return d
    return _desfase_rejilla_downbeats(res)




def desplazar_rejilla(res, seg):
    """Corre toda la grilla (tiempos y compases) `seg` segundos."""
    for k_full, k_draw in (("beats_full", "beats"), ("downbeats_full", "downbeats")):
        res[k_full] = [float(x) + seg for x in (res.get(k_full) or []) if float(x) + seg >= 0.0]
        res[k_draw] = [float(x) + seg for x in (res.get(k_draw) or []) if float(x) + seg >= 0.0]
    res["grid"] = 1
    res["manual"] = True
    return res




# ---------------------------------------------------------------------
# El "1" del compás, automático. Se decide por votación sobre TODOS los compases del tema (no solo el principio):
# la posición (0-3) donde el bombo pega más fuerte gana; el cambio de armonía (acorde nuevo) sirve de desempate suave.
# Se probó contra temas cuyo "1" había corregido Gustavo a mano: el acento del bombo acertó en todos, mientras que
# sumar energía general / subidas de volumen metía ruido en la cumbia, así que esas pistas no se usan.
# Además de la fase devuelve una confianza 0-1: si es baja, el "1" se marca como dudoso (igual se usa el mejor).
# ---------------------------------------------------------------------
UNO_CONF_MIN = 0.40
UNO_PESO_BOMBO, UNO_PESO_ARMONIA = 1.0, 0.25


def _z_robusto(v):
    v = np.asarray(v, dtype=np.float64)
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826
    if mad < 1e-9:
        mad = float(v.std()) or 1.0
    return np.clip((v - med) / mad, -3.0, 3.0)


UNO_MAX_BEATS, UNO_N_FFT = 100000, 2048  # se vota sobre TODO el tema (un tramo más corto cambiaba la respuesta en temas dudosos); n_fft 2048 = mismo resultado, ~2x más rápido


def detectar_uno(y, sr, beats, max_beats=None, n_fft=None):
    """Fase del "1" sobre la lista de beats: devuelve (fase 0-3, confianza 0-1) o None si hay muy pocos datos.
    `beats` = tiempos (s) de la grilla; el "1" cae en los beats j tales que j % 4 == fase."""
    max_beats = max_beats or UNO_MAX_BEATS; n_fft = n_fft or UNO_N_FFT
    b = np.asarray(beats, dtype=np.float64)
    if len(b) < 16 or len(y) < sr * 8:
        return None
    per = float(np.median(np.diff(b)))
    if not (0.25 < per < 1.5):
        return None
    if len(b) > max_beats:                          # tramo central; el inicio es múltiplo de 4, así la fase no cambia
        i0 = ((len(b) - max_beats) // 2) // 4 * 4
        b = b[i0:i0 + max_beats]
    t0 = max(0.0, float(b[0]) - 2.0 * per); t1 = min(len(y) / float(sr), float(b[-1]) + 2.0 * per)
    y = y[int(t0 * sr):int(t1 * sr)]
    b = b - t0
    n = len(b)
    hop = 512
    yl = lowpass_kick(y, sr, cutoff=150.0)
    env_l = librosa.onset.onset_strength(y=yl, sr=sr, hop_length=hop)
    ch = librosa.feature.chroma_stft(y=y, sr=sr, n_fft=n_fft, hop_length=hop)
    nf = min(len(env_l), ch.shape[1])
    env_l, ch = env_l[:nf], ch[:, :nf]
    fps = sr / float(hop)

    def fr(t):
        return int(np.clip(round(t * fps), 0, nf - 1))

    f_kick = np.zeros(n); f_arm = np.zeros(n)
    for i, t in enumerate(b):
        a, z = fr(t - 0.08 * per), fr(t + 0.12 * per) + 1       # el golpe: poco antes y poco después del beat
        f_kick[i] = float(env_l[a:z].max())
        p0, p1 = fr(t - per), max(fr(t - 0.05 * per), fr(t - per) + 1)
        q0, q1 = fr(t + 0.05 * per), max(fr(t + per), fr(t + 0.05 * per) + 1)
        pre, pos = ch[:, p0:p1].mean(axis=1), ch[:, q0:q1].mean(axis=1)
        den = float(np.linalg.norm(pre) * np.linalg.norm(pos))
        f_arm[i] = 1.0 - float(pre @ pos) / den if den > 1e-9 else 0.0       # cuánto cambia la armonía justo en este beat
    comb = UNO_PESO_BOMBO * _z_robusto(f_kick) + UNO_PESO_ARMONIA * _z_robusto(f_arm)
    fase = np.arange(n) % 4
    punt = np.array([comb[fase == p].mean() for p in range(4)])
    orden = np.argsort(punt)[::-1]
    margen = float(punt[orden[0]] - punt[orden[1]])
    ruido = float(comb.std()) * np.sqrt(2.0 / max(1.0, n / 4.0))      # error típico de la diferencia entre dos fases
    conf = float(np.clip(margen / (4.0 * ruido + 1e-9), 0.0, 1.0))
    return int(orden[0]), conf


def aplicar_uno(res, fase, conf):
    """Pone los downbeats del análisis `res` en los beats j con j % 4 == fase (modifica `res`)."""
    bt = np.asarray(res.get("beats_full") or [], dtype=np.float64)
    if len(bt) < 8:
        return False
    db = bt[np.arange(len(bt)) % 4 == int(fase)]
    def _ralo(v, tope):
        v = list(map(float, v))
        return v if len(v) <= tope else v[::len(v) // tope + 1]
    res["downbeats_full"] = list(map(float, db)); res["downbeats"] = _ralo(db, 200)
    res["uno_auto"] = 1
    res["uno_conf"] = float(conf)
    return True


def _analizar_uno(args):
    """Para análisis viejos sin el "1" automático: carga el audio y lo calcula (corre en el proceso aparte)."""
    path, beats = args
    y, sr = _cargar_mono(path)
    r = detectar_uno(y, sr, beats)
    try:
        kicks = [float(k) for k in detect_kicks(y, sr)]           # los análisis viejos tenían 0 bombos (error ya corregido)
    except Exception:
        kicks = []
    return {"uno": None if r is None else [int(r[0]), float(r[1])], "kicks": kicks}




def _limpiar_grilla_cache(res):
    """Versión barata (sin audio) para análisis ya guardados. Devuelve True si la grilla quedó regular."""
    if res.get("grid") == 1:
        return True
    try:
        ln = _linea_de_beats(res.get("beats_full") or [])
        if ln is None:
            return False
        _grilla_final(res, ln)
        return True
    except Exception:
        return False




# ---------- Tonalidad (rueda de Camelot) ----------
_PERFIL_MAYOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])


_PERFIL_MENOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


_NOTAS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]




def camelot_de(tonica, menor):
    """Nota (0=C … 11=B) y modo → código de la rueda de Camelot (ej. 8B = Do mayor, 8A = La menor)."""
    rel = (int(tonica) + 3) % 12 if menor else int(tonica)        # el menor comparte número con su relativo mayor
    num = ((rel * 7 + 8 - 1) % 12) + 1
    return f"{num}{'A' if menor else 'B'}"




def detectar_clave(y, sr):
    """Tonalidad por cromagrama + perfiles de Krumhansl. Devuelve dict con camelot (o None si no hay tono claro)."""
    try:
        if len(y) < sr * 5:
            return {"camelot": None}
        # el tramo central representa mejor al tema que la intro/outro; se acota para no tardar
        mid = len(y) // 2
        tramo = y[max(0, mid - sr * 45): mid + sr * 45]
        chroma = librosa.feature.chroma_stft(y=tramo, sr=sr, n_fft=4096, hop_length=2048)
        v = np.mean(chroma, axis=1)
        if not np.all(np.isfinite(v)) or float(np.sum(v)) <= 1e-9:
            return {"camelot": None}
        v = v / np.linalg.norm(v)
        mejores = []
        for modo, perfil in ((0, _PERFIL_MAYOR), (1, _PERFIL_MENOR)):
            for t in range(12):
                r = float(np.corrcoef(v, np.roll(perfil, t))[0, 1])
                mejores.append((r, t, modo))
        mejores.sort(reverse=True)
        r1, t1, m1 = mejores[0]
        r2 = mejores[1][0]
        return {"camelot": camelot_de(t1, bool(m1)), "tonica": int(t1), "menor": bool(m1),
                "nombre": _NOTAS[t1] + ("m" if m1 else ""), "conf": round(float(r1 - r2), 3)}
    except Exception:
        return {"camelot": None}




VOZ_DT = 0.5          # resolución (s) de las curvas de actividad guardadas en el análisis




def analizar_voz(y, sr):
    """Curvas por tramos de 0,5 s (valores 0..1): `voz` = cuánta voz/melodía hay en los medios (300 Hz–3,4 kHz,
    solo la parte armónica: sin golpes de caja ni platillos) y `bajo` = cuánta energía hay de bombo/bajo (40–200 Hz).
    Es una medida de energía, no un reconocedor de voces: una melodía de acordeón o de sintetizador en los medios
    también cuenta, y eso es justo lo que choca al mezclar."""
    try:
        from scipy.ndimage import uniform_filter1d
        hop = 1024
        S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
        f = librosa.fft_frequencies(sr=sr, n_fft=2048)
        # El HPSS (lo más lento del análisis) solo hace falta en la banda 300 Hz–3,4 kHz: se calcula sobre esas filas más 8 de
        # margen (el filtro mira ±7), con el mismo resultado dentro de la banda y ~3 veces más rápido.
        k0 = int(np.searchsorted(f, 300.0)) - 8; k1 = int(np.searchsorted(f, 3400.0, side="right")) + 8
        k0, k1 = max(0, k0), min(len(f), k1)
        Hb, _P = librosa.decompose.hpss(S[k0:k1], kernel_size=(15, 15))
        fb = f[k0:k1]
        suav = max(3, int(round(0.9 * sr / hop)))
        def norm(v):
            v = uniform_filter1d(v.astype(np.float64), suav)
            ref = float(np.percentile(v, 95)) or 1.0
            return np.clip(v / ref, 0.0, 1.0)
        # Voz/melodía = qué parte de TODA la energía está en los medios armónicos. Se mide contra la energía total (no
        # contra el propio máximo del tema), así un tema sin voz no se "infla" y uno con voz sí destaca.
        # VOZ_BAJA/VOZ_ALTA calibran el mapa a 0..1: por debajo de VOZ_BAJA no hay nada, por encima de VOZ_ALTA hay voz plena.
        VOZ_BAJA, VOZ_ALTA = 0.06, 0.22
        frac = Hb[(fb >= 300) & (fb <= 3400)].sum(axis=0) / (S.sum(axis=0) + 1e-9)
        voz = np.clip((uniform_filter1d(frac, suav) - VOZ_BAJA) / (VOZ_ALTA - VOZ_BAJA), 0.0, 1.0)
        bajo = norm(S[(f >= 40) & (f <= 200)].sum(axis=0))
        t_fr = np.arange(len(voz)) * hop / float(sr)
        t_g = np.arange(0.0, t_fr[-1] if len(t_fr) else 0.0, VOZ_DT)
        return {"voz": [round(float(v), 2) for v in np.interp(t_g, t_fr, voz)],
                "bajo": [round(float(v), 2) for v in np.interp(t_g, t_fr, bajo)],
                "voz_dt": VOZ_DT}
    except Exception:
        import traceback; traceback.print_exc()
        return {"voz": [], "bajo": [], "voz_dt": VOZ_DT}




def _analizar_clave(path):
    """Lo que le falte a un análisis viejo (tonalidad y curvas de voz/bajo), sin repetir el BPM. Devuelve un dict."""
    y, sr = _cargar_mono(path)
    extras = {"key": detectar_clave(y, sr)}
    extras.update(analizar_voz(y, sr))
    return extras




def _camelot_tuple(c):
    try:
        return int(c[:-1]), c[-1].upper()
    except Exception:
        return None




def compat_armonica(c1, c2):
    """2 = misma clave o relativa (suenan perfecto juntas) · 1 = vecina en la rueda (±1) · 0 = chocan · None = sin dato."""
    a, b = _camelot_tuple(c1) if c1 else None, _camelot_tuple(c2) if c2 else None
    if not a or not b:
        return None
    if a[0] == b[0]:
        return 2
    if a[1] == b[1] and (abs(a[0] - b[0]) % 12 in (1, 11)):
        return 1
    return 0




def _cargar_mono(path, sr=22050):
    """librosa.load, pero si el archivo tiene tramos dañados (libsndfile corta con 'Unspecified internal error' a mitad de la lectura)
    se analiza lo que se pudo leer, en vez de descartar el tema entero. Hacen falta al menos 15 s de audio."""
    try:
        return librosa.load(path, sr=sr, mono=True)
    except Exception as e1:
        try:
            import soundfile as sf
            bloques = []; n = 0
            with sf.SoundFile(path) as f:
                sr0 = f.samplerate
                while True:
                    try:
                        b = f.read(65536, dtype="float32", always_2d=True)
                    except Exception:
                        break                              # tramo dañado: se queda con lo leído hasta acá
                    if len(b) == 0:
                        break
                    bloques.append(b.mean(axis=1)); n += len(b)
            if n < 15 * sr0:
                raise e1
            y = np.concatenate(bloques)
            if sr0 != sr:
                y = librosa.resample(y, orig_sr=sr0, target_sr=sr)
            print(f"[análisis] {os.path.basename(str(path))}: archivo con tramos dañados, se analizaron {n / sr0:.0f} s")
            return y, sr
        except Exception:
            raise e1 from None


def _analizar_archivo(path):
    y, sr = _cargar_mono(path)

    rms_array = librosa.feature.rms(y=y, frame_length=2048, hop_length=1024)[0]
    rms_medio = float(np.mean(rms_array))
    if rms_medio > 1e-6:
        auto_gain = float(np.clip(TARGET_LUFS_RMS / rms_medio, 0.25, 4.0))
    else:
        auto_gain = 1.0

    tempo, beats = detect_beats(y, sr)
    tempo, beats = _plegar_tempo(tempo, beats, y, sr)
    # Si el nombre del archivo trae su BPM y el análisis no coincide (>6 %), se vuelve a seguir el pulso
    # usando ese BPM como guía (corrige errores de octava y de 3:2 que librosa comete).
    hint = _pista_bpm(path)
    if hint and abs(tempo - hint) / hint > 0.06:
        try:
            b2 = detect_beats_con_pista(y, sr, hint)
            g2 = _bpm_de_grilla(b2)
            if g2 and abs(g2 - hint) / hint < 0.06:
                beats, tempo = b2, g2
        except Exception:
            pass
    fino = _bpm_de_grilla(beats)                    # BPM preciso desde la grilla (si coincide con el estimado)
    if fino and abs(fino - tempo) / tempo < 0.04:
        tempo = fino
    duration = librosa.get_duration(y=y, sr=sr)
    kicks = detect_kicks(y, sr)
    downbeats = detect_downbeats(y, sr, beats)
    try:                                              # grilla regular: la mejor entre la de beats y la del audio
        env_g, t_g = _env_grave(y, sr)
        def _score(icpt, per):
            bt = icpt + per * np.arange(0, int(duration / per) + 1)
            bt = bt[(bt > 1.0) & (bt < duration - 1.0)]
            return float(np.mean(np.interp(bt, t_g, env_g))) if len(bt) else 0.0
        cands = []
        lb = _linea_de_beats(beats)
        if lb is not None:
            cands.append((_score(lb[0], lb[1]), (lb[0], lb[1])))
        ph, per, ratio = _linea_de_audio(env_g, t_g, tempo)
        brr = np.asarray(beats, dtype=np.float64)
        dentro = float(np.mean(np.abs(brr - (ph + per * np.round((brr - ph) / per))) < 0.06)) if len(brr) else 0.0
        if ratio >= 3.2 and dentro >= 0.4 and abs(60.0 / per - tempo) / tempo < 0.03:
            cands.append((_score(ph, per), (ph, per)))
        grilla = max(cands, key=lambda c: c[0])[1] if cands else None
    except Exception:
        import traceback; traceback.print_exc()
        grilla = None

    step = max(1, len(rms_array) // 2000)
    rms_small = rms_array[::step].tolist()

    if len(beats) > 400:
        skip = len(beats) // 400 + 1
        beats_draw = beats[::skip]
    else:
        beats_draw = beats
    if len(kicks) > 400:
        skip = len(kicks) // 400 + 1
        kicks_draw = kicks[::skip]
    else:
        kicks_draw = kicks
    if len(downbeats) > 200:
        skip = len(downbeats) // 200 + 1
        downbeats_draw = downbeats[::skip]
    else:
        downbeats_draw = downbeats

    res_out = {
        "bpm": float(tempo),
        "ver": 4,
        "duration": float(duration),
        "auto_gain": auto_gain,
        "beats": [float(b) for b in beats_draw],
        "kicks": [float(k) for k in kicks_draw],
        "downbeats": [float(d) for d in downbeats_draw],
        "beats_full": [float(b) for b in beats],
        "downbeats_full": [float(d) for d in downbeats],
        "kicks_full": [float(k) for k in kicks],
        "waveform": rms_small,
        "key": detectar_clave(y, sr),
    }
    res_out.update(analizar_voz(y, sr))
    if grilla is not None:
        _grilla_final(res_out, grilla)
    else:
        res_out["grid"] = 0                             # no hay pulso fijo claro: se queda la grilla detectada
    # (el "1" del compás NO se calcula acá, para que analizar la biblioteca siga rápido: se completa al cargar el tema en un deck)
    return res_out




def _guardar_cache(cache_key, result):
    from setup import almacen
    almacen.guardar(cache_key, result)        # atómico y comprimido (.json.gz); el formato viejo (.json) también se sigue pudiendo escribir
    try:
        from setup.indice import registrar        # el índice liviano (lo que usa la lista) se mantiene al día en cada guardado
        registrar(cache_key, result)
    except Exception:
        pass




def _leer_estereo_parcial(path, sr):
    """Para archivos con tramos dañados (libsndfile corta con 'Unspecified internal error' a mitad): lee lo que se pueda y se queda con eso.
    Devuelve (audio (n, 2), sr) o None si se leyeron menos de 15 s."""
    import soundfile as sf
    bloques = []; n = 0
    with sf.SoundFile(path) as f:
        sr0 = f.samplerate
        while True:
            try:
                b = f.read(65536, dtype="float32", always_2d=True)
            except Exception:
                break                                      # tramo dañado: se queda con lo leído hasta acá
            if len(b) == 0:
                break
            bloques.append(b if b.shape[1] >= 2 else np.repeat(b, 2, axis=1)); n += len(b)
    if n < 15 * sr0:
        return None
    y = np.concatenate(bloques)[:, :2]
    if sr0 != sr:
        y = librosa.resample(y.T, orig_sr=sr0, target_sr=sr).T
    print(f"[audio] {os.path.basename(str(path))}: archivo con tramos dañados, se cargaron {n / sr0:.0f} s")
    return np.ascontiguousarray(y, dtype=np.float32), sr


def load_audio_stereo(path, sr=44100):
    try:
        y, _ = librosa.load(path, sr=sr, mono=False)
    except Exception as e1:
        try:
            r = _leer_estereo_parcial(path, sr)
        except Exception:
            r = None
        if r is None:
            raise e1 from None
        return r
    if y.ndim == 1:
        y = np.stack([y, y])
    return y.T, sr
