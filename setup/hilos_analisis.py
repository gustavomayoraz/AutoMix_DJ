"""AutoMix — hilos (QThread) que analizan los temas sin trabar la interfaz."""
from pathlib import Path
import json
import os
import sys
import time
from collections import deque
from PySide6.QtCore import QThread, Signal
from setup.analisis import BPM_MAX, BPM_MIN, _guardar_cache, _limpiar_grilla_cache, _pista_bpm, analizar_archivo
from setup import almacen
from setup.base import CACHE_DIR


_PRIORITARIOS = 0              # análisis de un deck esperando: la biblioteca le cede el turno
_SONANDO = False               # algún deck está sonando (lo pone la ventana): el lote baja a UN solo proceso para no cortar el audio




class Analyzer(QThread):
    done = Signal(str, dict)
    uno_listo = Signal(str, object)       # (ruta, {"uno": [fase, confianza], "kicks": [...]}): lo que le faltaba a un análisis viejo

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        global _PRIORITARIOS
        _PRIORITARIOS += 1
        try:
            self._run()
        finally:
            _PRIORITARIOS = max(0, _PRIORITARIOS - 1)

    def _completar_uno(self, data):
        """El "1" del compás (votación sobre todo el tema) se calcula DESPUÉS de mostrar el tema, en el proceso aparte,
        y llega por la señal `uno_listo`. Así cargar un tema nunca espera por este cálculo."""
        if data.get("uno_auto") or len(data.get("beats_full") or []) < 16:
            return
        try:
            res = analizar_archivo(self.path, solo_uno=list(data["beats_full"]))
            self.uno_listo.emit(self.path, res)
        except Exception:
            import traceback; traceback.print_exc()

    def _run(self):
        try:
            cache_key = almacen.ruta_cache(self.path)             # dónde se guarda (formato nuevo, comprimido)
            hallado = almacen.buscar(self.path)                   # el análisis del tema, nuevo o viejo
            if hallado is not None:
                data = almacen.leer(hallado)
                hint = _pista_bpm(self.path)
                dudoso = (data.get("ver", 1) < 3 and hint is not None
                          and abs(float(data.get("bpm", 0)) - hint) / hint > 0.06)
                if dudoso or (data.get("ver", 1) < 2 and not (BPM_MIN <= float(data.get("bpm", 0)) < BPM_MAX)):
                    result = analizar_archivo(self.path)          # BPM de otra octava (ej. 161 en vez de 80): se rehace una vez
                    _guardar_cache(cache_key, result)
                    self.done.emit(self.path, result)
                    self._completar_uno(result)
                    return
                if data.get("grid") is None and data.get("ver", 1) < 4:
                    if _limpiar_grilla_cache(data):
                        data["ver"] = 4
                        _guardar_cache(cache_key, data)           # grilla regular lista: no se vuelve a calcular
                    else:
                        result = analizar_archivo(self.path)      # pulso irregular: se rehace una vez con la grilla del audio
                        _guardar_cache(cache_key, result)
                        self.done.emit(self.path, result)
                        self._completar_uno(result)
                        return
                if "key" not in data or "voz" not in data:       # caché viejo sin tonalidad / voz: se completa una vez
                    try:
                        data.update(analizar_archivo(self.path, solo_clave=True))
                        _guardar_cache(cache_key, data)
                    except Exception:
                        import traceback; traceback.print_exc()
                self.done.emit(self.path, data)     # caché viejo sin auto_gain: lo calcula el deck del audio ya cargado
                self._completar_uno(data)
                return

            result = analizar_archivo(self.path)
            _guardar_cache(cache_key, result)
            self.done.emit(self.path, result)
            self._completar_uno(result)
        except Exception as e:
            import traceback; traceback.print_exc()
            self.done.emit(self.path, {"error": str(e)})




def _ram_libre_gb():
    """Memoria RAM disponible en GB (Windows: GlobalMemoryStatusEx, sin librerías extra; Linux: /proc/meminfo). None si no se puede leer."""
    try:
        if os.name == "nt":
            import ctypes

            class _MEM(ctypes.Structure):
                _fields_ = [("largo", ctypes.c_ulong), ("uso", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("libre", ctypes.c_ulonglong), ("pf_total", ctypes.c_ulonglong), ("pf_libre", ctypes.c_ulonglong),
                            ("v_total", ctypes.c_ulonglong), ("v_libre", ctypes.c_ulonglong), ("v_ext", ctypes.c_ulonglong)]
            m = _MEM(); m.largo = ctypes.sizeof(_MEM)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.libre / 2 ** 30
            return None
        with open("/proc/meminfo", encoding="ascii") as f:
            for linea in f:
                if linea.startswith("MemAvailable:"):
                    return int(linea.split()[1]) / 2 ** 20
    except (OSError, ValueError, AttributeError):
        pass
    return None


class AnalizadorBiblioteca(QThread):
    progreso = Signal(int, int)
    tema_fallo = Signal(str, str)         # (ruta, motivo): el archivo no se pudo leer (dañado): va al historial de temas dañados
    en_curso = Signal(str)                # qué temas se están analizando ahora (para que la ventana muestre actividad)
    tema_listo = Signal(str)
    terminado = Signal()

    def __init__(self, rutas, forzar=False, modo_max=False, aparte=False):
        super().__init__()
        self.rutas = list(rutas)
        self.forzar = forzar
        self.aparte = aparte               # corre en el programa aparte (AnalizadorAutoMix.py): a toda velocidad pero dejando 2 núcleos y con prioridad algo menor, para que el resto del equipo siga respondiendo
        self.modo_max = modo_max           # análisis masivo con la ventana de progreso: todos los núcleos, prioridad normal
        self.cancelar = False

    def run(self):
        try:
            self._analizar_todo()
        except Exception:
            import traceback; traceback.print_exc()
        finally:
            self.terminado.emit()          # pase lo que pase, la lista se ordena con lo que haya

    @staticmethod
    def _limitar_por_ram(n):
        """Cada análisis ocupa ~1 a 2 GB de RAM (temas largos más): con la memoria casi llena la PC se traba por completo.
        Se baja la cantidad de procesos para contar ~2,5 GB por proceso (con picos) y dejar al menos 2,5 GB libres para el sistema."""
        libre = _ram_libre_gb()
        if libre is None:
            return n
        print(f"[análisis] RAM libre {libre:.1f} GB → máximo {max(1, int((libre - 2.5) // 2.5))} procesos")
        return max(1, min(n, int((libre - 2.5) // 2.5)))

    @staticmethod
    def _num_trabajadores():
        """Cuántos temas se analizan a la vez: todos los núcleos menos 2 (quedan para el audio y la ventana), entre 1 y 8.
        Cada análisis corre en su propio proceso (con prioridad baja), así que aprovecha todos los núcleos de verdad."""
        return AnalizadorBiblioteca._limitar_por_ram(max(1, min(8, (os.cpu_count() or 2) - 2)))

    def _n_trab(self):
        if self.modo_max:
            if self.aparte:
                return self._limitar_por_ram(max(1, min(10, (os.cpu_count() or 2) - 2)))
            return self._limitar_por_ram(max(1, min(12, os.cpu_count() or 2)))
        return self._num_trabajadores()

    def _en_vuelo_max(self, n):
        """Cuántos análisis puede haber en curso a la vez (corriendo + en cola). Con un deck sonando se baja a lo mínimo
        (1 por cada 4 trabajadores, al menos 1) para no arriesgar el audio; sin audio, uno más que los trabajadores."""
        if _SONANDO and not self.modo_max:
            return max(1, n // 4)
        return n + 1               # los n trabajadores ocupados + 1 esperando: la cola es corta, así al empezar a sonar un deck casi no queda trabajo en curso

    def _analizar_todo(self):
        self._hechos = set()
        n = self._n_trab()
        self.n_procesos = n
        if n >= 2 and len(self.rutas) >= 3:
            for intento in range(1, 4):                 # si el grupo de procesos se cuelga o cae, se vuelve a armar (hasta 3 veces) antes de pasar a de a un tema
                try:
                    self._analizar_paralelo(n)
                    return
                except Exception:
                    import traceback; traceback.print_exc()
                    if self.cancelar:
                        return
                    print(f"[análisis] el análisis en paralelo falló (intento {intento}/3)" + (": se vuelve a armar" if intento < 3 else ": se sigue de a un tema"))
        self._analizar_secuencial()

    def _avisar_fallo(self, ruta, e):
        """Si el error es de lectura del archivo (dañado, formato ilegible), se avisa para anotarlo en el historial de temas dañados."""
        txt = f"{type(e).__name__}: {e}"
        if any(m in txt for m in ("LibsndfileError", "NoBackendError", "Unspecified internal error", "Format not recognised", "System error")):
            self.tema_fallo.emit(ruta, "no se pudo leer el archivo al analizar (" + str(e).strip()[:80] + ")")

    def _rutas_livianas_primero(self):
        """Los temas ordenados de menor a mayor tamaño de archivo (un mix de una hora tarda minutos; un tema de 4 minutos, segundos)."""
        def tam(r):
            try:
                return os.path.getsize(r)
            except OSError:
                return 0
        return sorted(self.rutas, key=tam)

    @staticmethod
    def _timeout_de(ruta):
        """Tiempo máximo para un tema: 150 s como mínimo y 8 s por cada MB del archivo (un mix de 150 MB puede tardar 15-20 minutos con varios a la vez)."""
        from setup.analisis import TIMEOUT_ANALISIS
        try:
            mb = os.path.getsize(ruta) / 1048576
        except OSError:
            mb = 0.0
        return max(TIMEOUT_ANALISIS, 8.0 * mb)

    def _avisar_en_curso(self, pend):
        """Emite los nombres de los temas que se están analizando ahora (con su tamaño)."""
        nombres = []
        for it in list(pend)[:3]:
            ruta = it[1]
            try:
                mb = os.path.getsize(ruta) / 1048576
            except OSError:
                mb = 0.0
            nombres.append(f"{os.path.basename(ruta)} ({mb:.0f} MB)")
        self.en_curso.emit("🎧 Analizando ahora: " + "  ·  ".join(nombres) if nombres else "")

    def _tarea(self, ruta):
        """Qué hay que hacer con este tema: 'full' (análisis completo), 'clave' (completar tonalidad/voz) o None (ya está).
        Devuelve (tarea, cache_key)."""
        cache_key = almacen.ruta_cache(ruta)
        hallado = almacen.buscar(ruta)
        if self.forzar or hallado is None:
            return "full", cache_key
        data = almacen.leer(hallado)
        if "key" not in data or "voz" not in data:
            return "clave", cache_key
        return None, cache_key

    def _calentar(self):
        """numba compila (y guarda en su caché) las funciones de librosa la PRIMERA vez que se usan. Si varios procesos lo hacen
        a la vez en Windows pueden pisarse y morir ('access violation'). Por eso el primer tema de cada arranque se analiza
        solo, en el proceso de siempre; recién después se lanzan varios a la vez."""
        from setup import analisis as an
        if an._CALENTADO:
            return
        intentos = 0
        for i, ruta in enumerate(self._rutas_livianas_primero(), 1):
            if self.cancelar:
                return
            try:
                tarea, key = self._tarea(ruta)
            except Exception:
                continue
            if tarea is None:
                continue
            t0 = time.monotonic()
            intentos += 1
            try:
                res = an.analizar_archivo(ruta, solo_clave=(tarea == "clave"))
            except Exception as e:                   # un archivo dañado no debe tumbar el análisis en paralelo: se prueba con otro
                print(f"[análisis] no se pudo calentar con {os.path.basename(ruta)}: {e}")
                if intentos >= 6:
                    return
                continue
            if tarea == "full":
                _guardar_cache(key, res)
                print(f"Analizado: {os.path.basename(ruta)} -> {res['bpm']:.1f} BPM, clave {(res.get('key') or {}).get('camelot') or '?'}")
            else:
                data = almacen.leer(almacen.buscar(ruta) or key)
                data.update(res)
                _guardar_cache(key, data)
            self._hechos.add(ruta)
            self.tema_listo.emit(ruta); self.progreso.emit(i, len(self.rutas))
            return

    def _analizar_paralelo(self, n):
        from concurrent.futures._base import TimeoutError as _TO          # (imports directos: en Python 3.15 el paquete los carga 'perezosos' y no se pueden llamar)
        from concurrent.futures.process import ProcessPoolExecutor, BrokenProcessPool
        import multiprocessing
        from setup.analisis import TIMEOUT_ANALISIS, _analizar_archivo, _analizar_clave, _bajar_prioridad
        self._calentar()
        for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
            os.environ.setdefault(_v, "1")          # cada proceso usa UN hilo de cálculo: con varios procesos a la vez, no se pisan entre sí
        ctx = multiprocessing.get_context("spawn")
        pw = Path(sys.executable).with_name("pythonw.exe")             # el hijo no abre ventana negra
        if os.name == "nt" and pw.exists():
            ctx.set_executable(str(pw))
        total = len(self.rutas)
        if self.modo_max and self.aparte:
            os.environ["AUTOMIX_ANALISIS_MAX"] = "aparte"   # los hijos heredan esto: prioridad "por debajo de lo normal" (se aprovecha todo el procesador libre, pero ceden ante el resto)
        elif self.modo_max:
            os.environ["AUTOMIX_ANALISIS_MAX"] = "1"        # los procesos hijos heredan esto y NO bajan su prioridad (se pone ANTES de crear el grupo)
        else:
            os.environ.pop("AUTOMIX_ANALISIS_MAX", None)
        print(f"[análisis] biblioteca: {n} temas a la vez ({'máxima velocidad' if self.modo_max else 'prioridad baja'})")
        try:
            pool = ProcessPoolExecutor(max_workers=n, mp_context=ctx, initializer=_bajar_prioridad, max_tasks_per_child=20)    # cada proceso se renueva cada 20 temas: la memoria que acumula (librosa/numpy) se devuelve
        except TypeError:                                              # Python viejo sin max_tasks_per_child
            pool = ProcessPoolExecutor(max_workers=n, mp_context=ctx, initializer=_bajar_prioridad)
        pend = deque()                      # (i, ruta, cache_key, tarea, futuro, t0), en el orden de la lista
        sig = iter(enumerate(self._rutas_livianas_primero(), 1))        # primero los archivos chicos: el número avanza enseguida y los mixes enormes quedan para el final
        agotada = False
        falla = None
        try:
            while True:
                while (not agotada and len(pend) < self._en_vuelo_max(n) and not self.cancelar and _PRIORITARIOS == 0):
                    nxt = next(sig, None)
                    if nxt is None:
                        agotada = True
                        break
                    i, ruta = nxt
                    if ruta in self._hechos:            # ya se hizo en el calentamiento
                        continue
                    try:
                        tarea, key = self._tarea(ruta)
                    except Exception:
                        import traceback; traceback.print_exc()
                        tarea, key = None, None
                    if tarea is None:
                        self._hechos.add(ruta)
                        self.tema_listo.emit(ruta); self.progreso.emit(i, total)
                        continue
                    fut = pool.submit(_analizar_archivo if tarea == "full" else _analizar_clave, ruta)
                    pend.append((i, ruta, key, tarea, fut, time.monotonic()))
                    self._avisar_en_curso(pend)
                if self.cancelar:
                    break
                if not pend:
                    if agotada:
                        break
                    time.sleep(0.2)         # un deck espera su análisis: se le cede el turno
                    continue
                i, ruta, key, tarea, fut, t0 = pend.popleft()
                self._avisar_en_curso([(i, ruta)] + list(pend))
                try:
                    t_lim = time.monotonic() + self._timeout_de(ruta)
                    cancelado = False
                    while True:                         # se espera de a 0,25 s: así "Detener" y "Segundo plano" se atienden enseguida (antes había que esperar a que terminara el tema más lento)
                        try:
                            res = fut.result(timeout=0.25)
                            break
                        except _TO:
                            if self.cancelar:
                                cancelado = True
                                break
                            if time.monotonic() >= t_lim:
                                raise
                    if cancelado:
                        pend.appendleft((i, ruta, key, tarea, fut, t0))
                        break
                except (_TO, BrokenProcessPool) as e:
                    falla = e               # proceso colgado o caído: se corta el lote y se vuelve a armar
                    pend.appendleft((i, ruta, key, tarea, fut, t0))
                    if isinstance(e, _TO):
                        self._hechos.add(ruta)          # este tema se colgó: no se reintenta
                        self.progreso.emit(i, total)
                    break
                except Exception as e_tema:
                    import traceback; traceback.print_exc()      # ese tema falló: se sigue con los demás
                    self._avisar_fallo(ruta, e_tema)
                    self._hechos.add(ruta)
                    self.tema_listo.emit(ruta); self.progreso.emit(i, total)
                    continue
                try:
                    if tarea == "full":
                        _guardar_cache(key, res)
                        k = (res.get("key") or {}).get("camelot")
                        print(f"Analizado: {os.path.basename(ruta)} -> {res['bpm']:.1f} BPM, clave {k or '?'}")
                    else:
                        data = almacen.leer(almacen.buscar(ruta) or key)
                        data.update(res)
                        _guardar_cache(key, data)
                        print(f"Clave y voz: {os.path.basename(ruta)} -> {(data.get('key') or {}).get('camelot') or '?'}")
                    if time.monotonic() - t0 > 60.0:
                        print(f"[análisis] ese tema tardó {time.monotonic() - t0:.0f} s")
                except Exception:
                    import traceback; traceback.print_exc()
                self._hechos.add(ruta)
                self.tema_listo.emit(ruta); self.progreso.emit(i, total)
        finally:
            try:
                procs = list((getattr(pool, "_processes", None) or {}).values())
            except Exception:
                procs = []
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            for pr in procs:
                try:
                    pr.kill()
                except Exception:
                    pass
        if falla is not None:
            if isinstance(falla, BrokenProcessPool):          # un proceso murió de golpe: la caché de numba pudo quedar a medias
                try:
                    from setup.analisis import _limpiar_cache_numba
                    _limpiar_cache_numba()
                except Exception:
                    pass
            raise RuntimeError(f"{type(falla).__name__}")

    def _analizar_secuencial(self):
        total = len(self.rutas)
        for i, ruta in enumerate(self.rutas, 1):
            if self.cancelar:
                break
            if ruta in getattr(self, "_hechos", ()):
                continue
            while _PRIORITARIOS > 0 and not self.cancelar:      # un deck está esperando su análisis: pasa primero
                time.sleep(0.2)
            try:
                cache_key = almacen.ruta_cache(ruta)
                hallado = almacen.buscar(ruta)
                if self.forzar or hallado is None:
                    t0 = time.monotonic()
                    result = analizar_archivo(ruta)
                    _guardar_cache(cache_key, result)
                    k = (result.get("key") or {}).get("camelot")
                    print(f"Analizado: {os.path.basename(ruta)} -> {result['bpm']:.1f} BPM, clave {k or '?'}")
                    if time.monotonic() - t0 > 60.0:
                        print(f"[análisis] ese tema tardó {time.monotonic() - t0:.0f} s")
                else:                                   # análisis viejo sin tonalidad: se completa solo la clave
                    data = almacen.leer(hallado)
                    if "key" not in data or "voz" not in data:
                        data.update(analizar_archivo(ruta, solo_clave=True))
                        _guardar_cache(cache_key, data)
                        print(f"Clave y voz: {os.path.basename(ruta)} -> {(data.get('key') or {}).get('camelot') or '?'}")
            except Exception as e_tema:
                import traceback; traceback.print_exc()
                self._avisar_fallo(ruta, e_tema)
            self.tema_listo.emit(ruta)
            self.progreso.emit(i, total)




class RehacerUno(QThread):
    """Vuelve a calcular el "1" de un tema cuando la grilla cambió de octava (el compás ya no tiene el mismo largo).
    Corre en un hilo + el proceso de análisis aparte, así no traba el audio que está sonando."""
    listo = Signal(str, object)           # (ruta, [fase, confianza] o None)

    def __init__(self, path, beats):
        super().__init__()
        self.path = path
        self.beats = [float(b) for b in beats]

    def run(self):
        try:
            r = analizar_archivo(self.path, solo_uno=self.beats).get("uno")
        except Exception:
            import traceback; traceback.print_exc()
            r = None
        self.listo.emit(self.path, r)
