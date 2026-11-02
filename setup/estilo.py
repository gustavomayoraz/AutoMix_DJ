"""AutoMix — orden por estilo: ordena la lista del tema más parecido al menos parecido.
De cada tema se saca una "huella" con lo que ya guarda su análisis (tempo, energía, dinámica, voz, bajo, bombo y modo mayor/menor).
Con las huellas de toda la lista se arma una cadena: parte de un tema de referencia y en cada paso sigue el más parecido de los que quedan.
Las huellas se guardan en cache/huellas_estilo.json: la primera vez lee los análisis (lento con miles de temas); después solo los nuevos."""
import json
import math
import os
import numpy as np
from setup import almacen
from setup.base import CACHE_DIR

ARCHIVO = CACHE_DIR / "huellas_estilo.json"
NOMBRES = ("tempo", "energía", "picos", "dinámica", "voz", "voz_var", "bajo", "bajo_var", "bombo", "menor")
PESOS = np.array([2.0, 1.0, 0.5, 1.0, 0.8, 0.5, 0.8, 0.5, 1.0, 0.5], dtype=np.float32)      # cuánto pesa cada dato al comparar dos temas


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
    return [math.log2(min(220.0, max(40.0, bpm))),                     # tempo (en octavas: 90→180 es una de distancia)
            media,                                                      # energía: qué tan fuerte suena en promedio
            float(np.percentile(w, 90)),                                # picos: cuánto suben las partes fuertes
            float(w.std() / max(media, 1e-6)),                          # dinámica: qué tanto cambia de suave a fuerte
            float(v.mean()), float(v.std()),                            # voz: cuánta hay y cuánto cambia
            float(b.mean()), float(b.std()),                            # bajo: cuánto hay y cuánto cambia
            min(2.0, len(kicks) / max(1, len(beats))),                  # bombo: cuántos golpes de bombo por pulso
            menor]


def _leer_cache():
    try:
        d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
        if d.get("v") == 1 and isinstance(d.get("t"), dict):
            return d["t"]
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _guardar_cache(t):
    tmp = ARCHIVO.with_suffix(".tmp")
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        tmp.write_text(json.dumps({"v": 1, "t": t}), encoding="utf-8")
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


def matriz(huellas):
    """Huellas → matriz comparable: cada dato se lleva a la misma escala (respecto de toda la lista) y se pesa."""
    x = np.asarray(huellas, dtype=np.float64)
    sd = x.std(axis=0)
    sd[sd < 1e-9] = 1.0
    return ((x - x.mean(axis=0)) / sd * PESOS).astype(np.float32)


def cadena(z, inicio=0):
    """Orden de los índices de `z`: parte de `inicio` y en cada paso toma el más parecido (distancia más corta) de los que quedan."""
    n = len(z)
    if n == 0:
        return []
    a = np.array(z, dtype=np.float32, copy=True)
    ids = np.arange(n)
    nor = (a * a).sum(axis=1)
    m = n
    p = int(inicio) if 0 <= int(inicio) < n else 0
    orden = []
    while True:
        c = a[p].copy()
        orden.append(int(ids[p]))
        m -= 1
        if m == 0:
            break
        a[p], ids[p], nor[p] = a[m], ids[m], nor[m]                     # el último ocupa el lugar del elegido (así se achica sin copiar)
        d = nor[:m] - 2.0 * (a[:m] @ c) + float(c @ c)
        p = int(np.argmin(d))
    return orden


def ordenar(rutas, ruta_inicio=None, progreso=None):
    """(rutas en orden de más a menos parecido, cuántas tenían análisis). Las que no tienen análisis van al final, como estaban."""
    hu = calcular(rutas, progreso)
    con = [r for r in rutas if almacen.clave(r) in hu]
    sin = [r for r in rutas if almacen.clave(r) not in hu]
    if len(con) < 2:
        return list(rutas), len(con)
    z = matriz([hu[almacen.clave(r)] for r in con])
    ini = 0
    if ruta_inicio:
        k = os.path.normcase(os.path.abspath(ruta_inicio))
        ini = next((i for i, r in enumerate(con) if os.path.normcase(os.path.abspath(r)) == k), 0)
    return [con[i] for i in cadena(z, ini)] + sin, len(con)
