"""AutoMix — índice liviano de los temas analizados (SQLite, cache/indice.db).
Cada análisis guarda sus datos pesados (forma de onda, compases) en cache/<tema>.json; el índice guarda SOLO lo que necesita la lista
(BPM, clave, duración, si tiene grilla y voz). Así la lista se arma con una consulta en vez de leer miles de .json, y el analizador aparte
y el reproductor se comunican por acá: uno escribe, el otro pregunta "qué hay nuevo desde la última vez"."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from setup.base import CACHE_DIR

DB = CACHE_DIR / "indice.db"
_lock = threading.Lock()
_listo = False
_avisado = False


def _avisar(e):
    """Un fallo de la base no debe romper nada (la lista lee los .json), pero queda anotado una vez en la consola."""
    global _avisado
    if not _avisado:
        _avisado = True
        print(f"[índice] no se pudo usar la base de datos ({type(e).__name__}: {e}): la lista usa los .json de la caché")


def _disco_fijo():
    """False si la carpeta está en un pendrive o una unidad de red: ahí el modo WAL de SQLite (memoria compartida) puede fallar."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        unidad = os.path.splitdrive(str(DB))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(unidad) == 3         # DRIVE_FIXED
    except Exception:
        return True

_SQL = """CREATE TABLE IF NOT EXISTS temas (
    tema TEXT PRIMARY KEY,          -- nombre del .json de la caché (sin extensión)
    bpm REAL, camelot TEXT, nombre TEXT,
    hay_clave INTEGER NOT NULL DEFAULT 0, frase INTEGER NOT NULL DEFAULT 0, voz INTEGER NOT NULL DEFAULT 0,
    dur REAL, upd INTEGER NOT NULL)"""


def _conectar():
    """Una conexión nueva por uso (barato, y sirve desde cualquier hilo o proceso)."""
    global _listo
    con = sqlite3.connect(str(DB), timeout=30)
    if not _listo:
        with _lock:
            try:
                if _disco_fijo():
                    con.execute("PRAGMA journal_mode=WAL")       # uno escribe mientras otros leen (en pendrives se usa el modo común)
            except sqlite3.DatabaseError:
                pass
            con.execute(_SQL)
            con.execute("CREATE INDEX IF NOT EXISTS ix_upd ON temas(upd)")
            con.commit()
            _listo = True
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def fila_de(tema, d):
    """(tema, bpm, camelot, nombre, hay_clave, frase, voz, dur, upd) a partir del dict del análisis (el mismo criterio que Biblioteca.info)."""
    try:
        b = d.get("bpm"); bpm = float(b) if b is not None else None
    except (TypeError, ValueError):
        bpm = None
    k = d.get("key")
    hay = 1 if "key" in d else 0
    cam = (k or {}).get("camelot") if isinstance(k, dict) else None
    nom = (k or {}).get("nombre") if isinstance(k, dict) else None
    fr = 1 if (len(d.get("downbeats_full") or d.get("downbeats") or []) >= 9 and d.get("grid") != 0) else 0
    voz = 1 if "voz" in d else 0
    try:
        dur = float(d.get("duration") or 0) or None
    except (TypeError, ValueError):
        dur = None
    return (tema, bpm, cam, nom, hay, fr, voz, dur, time.time_ns())


_UPSERT = "INSERT OR REPLACE INTO temas(tema,bpm,camelot,nombre,hay_clave,frase,voz,dur,upd) VALUES (?,?,?,?,?,?,?,?,?)"


def registrar(cache_key, d):
    """Anota (o actualiza) un tema en el índice. Se llama cada vez que se guarda un análisis en la caché. Nunca debe romper el guardado."""
    try:
        con = _conectar()
        try:
            from setup.almacen import tema_de
            con.execute(_UPSERT, fila_de(tema_de(cache_key), d))
            con.commit()
        finally:
            con.close()
    except (sqlite3.Error, OSError) as e:
        _avisar(e)


def registrar_lote(filas):
    """Anota muchos temas en una sola transacción: lista de tuplas fila_de()."""
    if not filas:
        return
    con = _conectar()
    try:
        con.executemany(_UPSERT, filas)
        con.commit()
    finally:
        con.close()


def olvidar(tema):
    try:
        con = _conectar()
        try:
            con.execute("DELETE FROM temas WHERE tema=?", (tema,)); con.commit()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        pass


def todo():
    """{tema: (bpm, camelot, nombre, hay_clave, frase, voz, dur)} de TODO el índice, de una sola consulta."""
    con = _conectar()
    try:
        return {r[0]: r[1:] for r in con.execute("SELECT tema,bpm,camelot,nombre,hay_clave,frase,voz,dur FROM temas")}
    finally:
        con.close()


def novedades(desde, limite=5000):
    """Temas anotados después de `desde` (marca de tiempo): ([(tema, bpm, camelot, nombre, hay_clave, frase, voz, dur)], nueva_marca)."""
    con = _conectar()
    try:
        filas = con.execute("SELECT tema,bpm,camelot,nombre,hay_clave,frase,voz,dur,upd FROM temas WHERE upd>? ORDER BY upd LIMIT ?",
                            (desde, limite)).fetchall()
    finally:
        con.close()
    return [f[:8] for f in filas], (filas[-1][8] if filas else desde)


def marca_actual():
    """La marca más reciente del índice (para pedir después solo lo nuevo)."""
    con = _conectar()
    try:
        r = con.execute("SELECT MAX(upd) FROM temas").fetchone()
        return r[0] or 0
    finally:
        con.close()


def contar():
    con = _conectar()
    try:
        return con.execute("SELECT COUNT(*) FROM temas").fetchone()[0]
    finally:
        con.close()


def migrar(temas_json=None, progreso=None, cancelar=None):
    """Llena el índice con los .json de la caché que todavía no están. Devuelve cuántos agregó.
    `progreso(i, total)` y `cancelar()` son opcionales. Lee de a un archivo y guarda de a 200."""
    from setup.almacen import ANALISIS_DIR, leer, tema_de
    existentes = set(todo().keys())
    if temas_json is None:
        temas_json = list(ANALISIS_DIR.glob("*.json.gz"))
    pend = [p for p in temas_json if tema_de(p) not in existentes]
    lote, n = [], 0
    for i, p in enumerate(pend, 1):
        if cancelar is not None and cancelar():
            break
        try:
            d = leer(p)
            if isinstance(d, dict) and "bpm" in d:
                lote.append(fila_de(tema_de(p), d))
        except (OSError, ValueError, EOFError):
            pass
        if len(lote) >= 200:
            registrar_lote(lote); n += len(lote); lote = []
        if progreso is not None and (i % 50 == 0 or i == len(pend)):
            progreso(i, len(pend))
    registrar_lote(lote); n += len(lote)
    return n


def por_temas(temas):
    """{tema: (bpm, camelot, nombre, hay_clave, frase, voz, dur)} de esos temas (en tandas, para no pasar el límite de variables de SQLite)."""
    temas = list(temas)
    out = {}
    con = _conectar()
    try:
        for i in range(0, len(temas), 500):
            tanda = temas[i:i + 500]
            q = "SELECT tema,bpm,camelot,nombre,hay_clave,frase,voz,dur FROM temas WHERE tema IN (%s)" % ",".join("?" * len(tanda))
            for r in con.execute(q, tanda):
                out[r[0]] = r[1:]
    finally:
        con.close()
    return out
