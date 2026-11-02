"""AutoMix — almacén de los análisis de los temas.
· Viven en la carpeta de datos permanente (C:\\AutoMix_datos\\analisis), fuera de la carpeta del programa.
· Se guardan comprimidos (.json.gz): ocupan mucho menos y se leen igual.
· Cada tema se reconoce por su NOMBRE + TAMAÑO (no por la carpeta): si lo mueves de carpeta no se vuelve a analizar, y dos temas
  distintos que se llamen igual ("04.mp3" de dos discos) ya no comparten el análisis.
· Los análisis viejos (nombre.json sin comprimir, en la carpeta de datos o en la cache vieja del programa) se siguen leyendo,
  y compactar_legado() los pasa al formato nuevo de a poco, en segundo plano."""
import gzip
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path
from setup.base import CACHE_DIR, LEGADO_DIR

ANALISIS_DIR = CACHE_DIR / "analisis"
_MAX_NOMBRE = 120                  # nombres de archivo más largos se acortan (con un código al final para que no se pisen)
_claves = {}                       # ruta -> clave (solo se recuerdan las que existen: si el archivo aparece después, cambia el tamaño)


def clave(ruta):
    """Identidad del tema: 'nombre__tamaño'. Es el nombre de su archivo de análisis y su fila en el índice."""
    ruta = str(ruta)
    k = _claves.get(ruta)
    if k is not None:
        return k
    nombre = Path(ruta).stem
    try:
        tam = os.stat(ruta).st_size
        existe = True
    except OSError:
        tam, existe = 0, False
    if len(nombre) > _MAX_NOMBRE:
        nombre = nombre[:_MAX_NOMBRE - 9].rstrip(" .") + "~" + hashlib.md5(nombre.encode("utf-8", "ignore")).hexdigest()[:8]
    k = f"{nombre}__{tam}"
    if existe:
        _claves[ruta] = k
    return k


def ruta_cache(ruta):
    """Dónde se guarda (formato nuevo, comprimido) el análisis de este tema."""
    return ANALISIS_DIR / (clave(ruta) + ".json.gz")


def tema_de(archivo):
    """Nombre del tema en el índice a partir de su archivo de análisis (sin .json.gz / .json)."""
    n = Path(archivo).name
    for suf in (".json.gz", ".json"):
        if n.endswith(suf):
            return n[:-len(suf)]
    return Path(archivo).stem


def _viejos(ruta):
    """Dónde pudo haber guardado antes el análisis: nombre.json en la carpeta de datos o en la cache vieja del programa."""
    nombre = Path(str(ruta)).stem
    vistos = []
    for base in (CACHE_DIR, LEGADO_DIR):
        q = base / (nombre + ".json")
        if q not in vistos:
            vistos.append(q)
    return vistos


def buscar(ruta):
    """Archivo con el análisis de este tema (nuevo o viejo) o None si no está analizado."""
    p = ruta_cache(ruta)
    if p.exists():
        return p
    for q in _viejos(ruta):
        if q.exists():
            return q
    return None


def leer(archivo):
    """dict del análisis, sea .json.gz (nuevo) o .json (viejo)."""
    archivo = str(archivo)
    if archivo.endswith(".gz"):
        with gzip.open(archivo, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(archivo, encoding="utf-8") as f:
        return json.load(f)


def guardar(archivo, resultado):
    """Escribe el análisis (comprimido si el archivo termina en .gz) de forma atómica: nunca queda un archivo a medias."""
    archivo = Path(archivo)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(archivo) + ".tmp")
    txt = json.dumps(resultado)                     # (dumps usa el codificador rápido en C; json.dump a un archivo usa el lento)
    if archivo.suffix == ".gz":
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
            f.write(txt)
    else:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(txt)
    os.replace(tmp, archivo)


def compactar_legado(rutas, cancelar=None, progreso=None, en_pausa=None):
    """Pasa al formato nuevo los análisis viejos de los temas de la lista. Solo toca los temas cuyo nombre es único en la lista
    (si dos temas se llaman igual no se sabe de cuál es el análisis viejo: se dejan como están). Lo viejo que está en la carpeta de
    datos se borra después de pasarlo; lo de la cache vieja del programa no se toca. Devuelve cuántos pasó.
    `en_pausa()`: si devuelve True (por ejemplo suena música) espera sin tocar nada. Cada conversión va seguida de una pausa corta."""
    from setup import indice
    rutas = list(rutas)
    cuenta = Counter(Path(r).stem for r in rutas)
    n = 0
    for i, r in enumerate(rutas):
        if cancelar is not None and cancelar():
            break
        if progreso is not None and i % 500 == 0:
            progreso(i, len(rutas))
        nombre = Path(r).stem
        while en_pausa is not None and en_pausa() and not (cancelar is not None and cancelar()):
            time.sleep(0.5)
        if cuenta[nombre] != 1:
            continue
        nuevo = ruta_cache(r)
        viejo = next((q for q in _viejos(r) if q.exists()), None)
        if viejo is None:
            continue
        try:
            if not nuevo.exists():
                d = leer(viejo)
                if not isinstance(d, dict) or "bpm" not in d:
                    continue
                guardar(nuevo, d)
                indice.registrar(nuevo, d)
                n += 1
                time.sleep(0.03)                          # (no acapara el procesador ni el disco mientras se usa el programa)
            if viejo.parent == CACHE_DIR:                 # lo viejo de la carpeta de datos ya está pasado: se libera el lugar
                viejo.unlink()
                indice.olvidar(nombre)
        except (OSError, ValueError):
            continue
    return n
