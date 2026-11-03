"""AutoMix — orden por estilo: ordena la lista como una planilla, columna por columna.
Orden de las columnas: 1) BPM (de menor a mayor, redondeado al entero) · 2) tonalidad (por la rueda Camelot: 1A, 1B, 2A, 2B…) ·
3) voz (de menos a más) · 4) bajo (de menos a más) · 5) bombos (de menos a más) · 6) compás firme primero · 7) forma de onda (energía, de menos a más).
Cada columna solo desempata a la anterior. Voz, bajo y bombos se agrupan en niveles (cuartiles / tercios de TODA la lista): sin eso, como los
números tienen decimales, la primera columna nunca empataría y las demás no servirían.
De cada tema se saca una "huella" con lo que ya guarda su análisis; se guarda en huellas_estilo.json (la primera vez lee los análisis,
lento con miles de temas; después solo los nuevos)."""
import json
import math
import os
import numpy as np
from setup import almacen
from setup.base import CACHE_DIR

ARCHIVO = CACHE_DIR / "huellas_estilo.json"
VERSION = 2
NOMBRES = ("tempo", "energía", "picos", "dinámica", "voz", "voz_var", "bajo", "bajo_var", "bombo", "menor", "camelot", "compás")
_SIN_TONO = 99                                                                   # sin tonalidad: al final de su tramo de BPM


def _camelot(an):
    """Posición en la rueda Camelot (1A=0, 1B=1, 2A=2 …) o 99 si el análisis no trae tonalidad."""
    c = str(((an.get("key") or {}).get("camelot")) or "").strip().upper()
    if len(c) >= 2 and c[-1] in "AB" and c[:-1].isdigit() and 1 <= int(c[:-1]) <= 12:
        return float((int(c[:-1]) - 1) * 2 + (1 if c[-1] == "B" else 0))
    return float(_SIN_TONO)


def huella(an):
    """Lista de números que describe el sonido del tema, o None si su análisis no trae lo necesario."""
    try:
        w = np.asarray(an["waveform"], dtype=np.float32)
        v = np.asarray(an["voz"], dtype=np.float32)
        b = np.asarray(an["bajo"], dtype=np.float32)
        bpm = float(an["bpm"])
    except (KeyError, TypeError, ValueError):
        return None
    if w.size < 8 or v.size < 2 or b.size < 2 or not math.isfinite(bpm) or bpm <= 0:
        return None
    media = float(w.mean())
    beats = an.get("beats") or []
    kicks = an.get("kicks") or []
    menor = 1.0 if (an.get("key") or {}).get("menor") else 0.0
    firme = 1.0 if (len(an.get("downbeats_full") or an.get("downbeats") or []) >= 9 and an.get("grid") != 0) else 0.0
    return [math.log2(min(220.0, max(40.0, bpm))),                     # tempo (en octavas)
            media,                                                      # energía: qué tan fuerte suena en promedio
            float(np.percentile(w, 90)),                                # picos: cuánto suben las partes fuertes
            float(w.std() / max(media, 1e-6)),                          # dinámica: qué tanto cambia de suave a fuerte
            float(v.mean()), float(v.std()),                            # voz: cuánta hay y cuánto cambia
            float(b.mean()), float(b.std()),                            # bajo: cuánto hay y cuánto cambia
            min(2.0, len(kicks) / max(1, len(beats))),                  # bombo: cuántos golpes de bombo por pulso
            menor,
            _camelot(an),                                               # tonalidad en la rueda Camelot
            firme]                                                      # compás firme (grilla regular) o no


def _leer_cache():
    try:
        d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
        if d.get("v") == VERSION and isinstance(d.get("t"), dict):
            return d["t"]
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _guardar_cache(t):
    tmp = ARCHIVO.with_suffix(".tmp")
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        tmp.write_text(json.dumps({"v": VERSION, "t": t}), encoding="utf-8")
        os.replace(tmp, ARCHIVO)
    except OSError:
        pass


def calcular(rutas, progreso=None):
    """{clave del tema: huella} para los temas que tienen análisis. Usa la caché de huellas; solo lee el análisis de los que cambiaron."""
    t = _leer_cache()
    sal = {}
    cambios = False
    n = len(rutas)
    for i, r in enumerate(rutas):
        if progreso is not None and i % 200 == 0:
            progreso(i, n)
        k = almacen.clave(r)
        if k in sal:
            continue
        p = almacen.buscar(r)
        if p is None:
            continue                                                    # sin análisis
        try:
            mt = int(p.stat().st_mtime)
        except OSError:
            continue
        fila = t.get(k)
        if fila and fila[0] == mt and len(fila) == len(NOMBRES) + 1:
            sal[k] = fila[1:]
            continue
        try:
            d = almacen.leer(p)
        except (OSError, ValueError, EOFError):
            continue
        h = huella(d) if isinstance(d, dict) else None
        if h is not None:
            t[k] = [mt] + h
            sal[k] = h
            cambios = True
    if progreso is not None:
        progreso(n, n)
    if cambios:
        _guardar_cache(t)
    return sal


def _niveles(valores, n):
    """Nivel (0 … n-1) de cada valor según los cortes por cuantiles de TODA la lista: cada nivel junta más o menos la misma cantidad de temas."""
    x = np.asarray(valores, dtype=np.float64)
    cortes = np.unique(np.quantile(x, [k / n for k in range(1, n)]))
    return np.searchsorted(cortes, x, side="right")


def ordenar(rutas, ruta_inicio=None, progreso=None):
    """(rutas ordenadas por columnas, cuántas tenían análisis). Las que no tienen análisis van al final, como estaban.
    `ruta_inicio` ya no se usa (el orden por columnas no parte de ningún tema); se deja para no cambiar quien llama."""
    hu = calcular(rutas, progreso)
    con = [r for r in rutas if almacen.clave(r) in hu]
    sin = [r for r in rutas if almacen.clave(r) not in hu]
    if len(con) < 2:
        return list(rutas), len(con)
    h = np.asarray([hu[almacen.clave(r)] for r in con], dtype=np.float64)
    bpm = np.rint(2.0 ** h[:, 0]).astype(np.int64)                      # 1) BPM entero
    tono = h[:, 10]                                                     # 2) rueda Camelot
    voz = _niveles(h[:, 4], 4)                                          # 3) voz: 4 niveles
    bajo = _niveles(h[:, 6], 4)                                         # 4) bajo: 4 niveles
    bombo = _niveles(h[:, 8], 3)                                        # 5) bombos: 3 niveles
    compas = 1.0 - h[:, 11]                                             # 6) compás firme primero
    onda = h[:, 1]                                                      # 7) forma de onda (energía)
    orden = sorted(range(len(con)), key=lambda i: (bpm[i], tono[i], voz[i], bajo[i], bombo[i], compas[i], onda[i], str(con[i]).lower()))
    return [con[i] for i in orden] + sin, len(con)
