"""AutoMix — estadísticas de los temas.
· Reproducciones: un tema suma una cuando suena de verdad el % de su duración que se elige en ⚙ Ajustes (50 % por defecto; los saltos con el mouse no cuentan).
· Estrellas (1 a 5): salen solas, según cuánto suena el tema comparado con los demás. Tú no las tocas.
· 👍: marca manual, aparte de las estadísticas.
Todo queda en cache/estadisticas_temas.json (se puede borrar)."""
import json
import math
import os
import time
from setup.base import CACHE_DIR

ARCHIVO = CACHE_DIR / "estadisticas_temas.json"
UMBRAL = 0.5            # (respaldo) fracción del tema que tiene que sonar para sumar una reproducción; el valor que vale es el de ⚙ Ajustes
MIN_REF = 5             # con pocas reproducciones en total, las estrellas suben despacio (5 reproducciones = 5 estrellas)
PERCENTIL = 0.9         # referencia de las 5 estrellas: lo que suenan los temas más tocados (un solo tema muy pasado no aplasta al resto)


def _umbral() -> float:
    """Fracción del tema que tiene que sonar para contar una reproducción (⚙ Ajustes → Estadísticas); entre 10 % y 95 %."""
    try:
        from setup import cruce_auto
        return min(0.95, max(0.10, float(cruce_auto.EST_PORC) / 100.0))
    except (ImportError, AttributeError, TypeError, ValueError):
        return UMBRAL


def _clave(ruta: str) -> str:
    return os.path.normcase(os.path.abspath(ruta))


class Estadisticas:
    """Reproducciones, estrellas y 👍 de cada tema (clave: ruta normalizada)."""

    def __init__(self) -> None:
        self.datos: dict[str, dict] = {}
        self._ref: int | None = None
        self._cargar()

    # ---------- archivo ----------
    def _cargar(self) -> None:
        try:
            d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
            self.datos = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            self.datos = {}

    def _guardar(self) -> bool:
        tmp = ARCHIVO.with_suffix(".tmp")
        try:
            CACHE_DIR.mkdir(exist_ok=True)
            tmp.write_text(json.dumps(self.datos, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, ARCHIVO)
            return True
        except OSError:
            return False

    def _fila(self, ruta: str) -> dict:
        k = _clave(ruta)
        f = self.datos.get(k)
        if f is None:
            f = self.datos[k] = {"ruta": ruta, "nombre": os.path.splitext(os.path.basename(ruta))[0], "n": 0, "like": False, "ult": 0}
        return f

    # ---------- reproducciones y 👍 ----------
    def sumar(self, ruta: str) -> int:
        """Anota una reproducción (el tema sonó el % elegido). Devuelve el total del tema."""
        f = self._fila(ruta)
        f["n"] = int(f.get("n", 0)) + 1
        f["ult"] = int(time.time())
        f["ruta"] = ruta
        self._ref = None
        self._guardar()
        return f["n"]

    def borrar_reproducciones(self) -> int:
        """Pone en cero las reproducciones de todos los temas (y con eso sus estrellas). Los 👍 se mantienen. Devuelve a cuántos temas se les borró."""
        n = 0
        for v in self.datos.values():
            if int(v.get("n", 0)) > 0:
                n += 1
            v["n"] = 0
            v["ult"] = 0
        self._limpiar_vacios()
        self._ref = None
        self._guardar()
        return n

    def poner_like(self, rutas, valor: bool) -> None:
        """Marca o desmarca el 👍 de uno o varios temas."""
        for r in rutas:
            f = self._fila(r)
            f["like"] = bool(valor)
        self._limpiar_vacios()
        self._guardar()

    def _limpiar_vacios(self) -> None:
        """Un tema sin reproducciones y sin 👍 no necesita ficha."""
        for k in [k for k, v in self.datos.items() if int(v.get("n", 0)) <= 0 and not v.get("like")]:
            del self.datos[k]

    def reproducciones(self, ruta: str) -> int:
        f = self.datos.get(_clave(ruta))
        return int(f.get("n", 0)) if f else 0

    def like(self, ruta: str) -> bool:
        f = self.datos.get(_clave(ruta))
        return bool(f.get("like")) if f else False

    # ---------- estrellas ----------
    def referencia(self) -> int:
        """Reproducciones que equivalen a 5 estrellas: las de los temas más tocados (mínimo MIN_REF)."""
        if self._ref is None:
            ns = sorted(int(v.get("n", 0)) for v in self.datos.values() if int(v.get("n", 0)) > 0)
            r = ns[int(PERCENTIL * (len(ns) - 1))] if ns else 0
            self._ref = max(MIN_REF, r)
        return self._ref

    def estrellas(self, ruta: str) -> int:
        """0 si todavía no tiene reproducciones; si no, de 1 a 5 según cuánto suena respecto a los demás."""
        return self._estrellas_de(self.reproducciones(ruta))

    def _estrellas_de(self, n: int) -> int:
        if n <= 0:
            return 0
        return max(1, min(5, math.ceil(5 * n / self.referencia())))

    def filas(self) -> list[dict]:
        """Todos los temas con estadísticas: lista de dicts (ruta, nombre, n, estrellas, like, ult)."""
        out = []
        for v in self.datos.values():
            n = int(v.get("n", 0))
            out.append({"ruta": v.get("ruta", ""), "nombre": v.get("nombre", ""), "n": n, "estrellas": self._estrellas_de(n),
                        "like": bool(v.get("like")), "ult": int(v.get("ult", 0))})
        return out


class Seguimiento:
    """Mide cuánto sonó de verdad un tema en un deck (por vuelta). Se llama en cada tick con el tema cargado."""

    def __init__(self) -> None:
        self.path: str | None = None
        self.pos = 0.0
        self.acum = 0.0
        self.contado = False

    def actualizar(self, path: str | None, sonando: bool, pos: float, dur: float) -> bool:
        """True en el momento exacto en que el tema pasa el % escuchado elegido en ⚙ Ajustes (una sola vez por vuelta)."""
        if not path or dur <= 0.0:
            return False
        if path != self.path:                              # tema nuevo: se empieza de cero
            self.path, self.pos, self.acum, self.contado = path, pos, 0.0, False
            return False
        if pos < self.pos - 0.5 * dur:                     # volvió al principio (otra vuelta del mismo tema)
            self.acum, self.contado = 0.0, False
        elif sonando and 0.0 < pos - self.pos < 2.0:       # avance normal: suma; un salto con el mouse (>2 s) no suma
            self.acum += pos - self.pos
        self.pos = pos
        if not self.contado and self.acum >= _umbral() * dur:
            self.contado = True
            return True
        return False


EST = Estadisticas()
