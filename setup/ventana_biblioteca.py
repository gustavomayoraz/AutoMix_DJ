"""AutoMix — biblioteca: análisis de los temas, orden, carga en los decks."""
import os
import threading
import time
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMessageBox
from PySide6.QtCore import QTimer, Qt
from setup.ajustes import SUPPORTED
from setup.analisis import _mantener_vivo
from setup.analizador_aparte import aparte_activo, lanzar_aparte
from setup.base import log
from setup.config import guardar_config, leer_config
from setup.deck import Deck
from setup import estilo
from setup.estadisticas import EST, Seguimiento
from setup.dialogo_analisis import DialogoAnalisis
from setup.hilos_analisis import AnalizadorBiblioteca

UMBRAL_APARTE = 1000       # con más temas que esto por analizar se ofrece el programa aparte (AnalizadorAutoMix.py)
UMBRAL_MASIVO = 100        # a partir de esta cantidad de temas por analizar aparece la ventana de análisis a máxima velocidad


class BibliotecaMixin:
    """Biblioteca: análisis de los temas, orden, carga en los decks (parte de la ventana principal)."""


    _tildado_deck = {}                # {'A': ruta, 'B': ruta}: el tema que ya se tildó al empezar a sonar en cada deck
    _seguimientos = None              # {'A': Seguimiento, 'B': Seguimiento}: cuánto sonó de verdad cada tema de los decks

    def _contar_reproducciones(self):
        """Cada tick: el tema que empieza a sonar en un deck se tilda; si pasa el 50 % escuchado, suma una reproducción y la lista actualiza sus estrellas."""
        if self._seguimientos is None:
            self._seguimientos = {"A": Seguimiento(), "B": Seguimiento()}
            self._tildado_deck = {}
        for nombre, d in (("A", self.deck_a), ("B", self.deck_b)):
            audio = d.audio
            if audio is None or not d.path or not d.sr:
                continue
            if d.playing and self._tildado_deck.get(nombre) != d.path:
                self._tildado_deck[nombre] = d.path
                self.biblioteca.marcar_tocado(d.path)             # apenas suena: queda tildado para que no se repita solo (una vez por tema: si lo destildas a mano, no se vuelve a tildar)
            if self._seguimientos[nombre].actualizar(d.path, d.playing, d.position_seconds(), len(audio) / d.sr):
                n = EST.sumar(d.path)
                log("estadisticas", f"{os.path.basename(d.path)}: {n} reproducciones")
                self.biblioteca.refrescar_estadisticas()

    def _abrir_estadisticas(self):
        from setup.dialogo_estadisticas import DialogoEstadisticas
        DialogoEstadisticas(self, self.biblioteca.refrescar_estadisticas).exec()

    _analizador_bib = None
    _fallidos = None
    _ordenar_al_terminar = False
    _modo_siguiente = None            # 'max' / 'suave': al terminar el análisis en curso (cancelado a propósito) se reinicia en ese modo
    _dlg_analisis = None
    _oculta_por_analisis = False
    _dlg_prep = None                  # ventanita de "Acelerar" que se abre al instante, antes de que el análisis anterior termine de cerrarse
    _prep_modo = None                 # 'suave' / 'cancelar': lo que se eligió en esa ventanita mientras se preparaba
    _proc_aparte = None               # el programa aparte que está analizando (subprocess.Popen)
    _t_aparte = None
    _aparte_total = 0
    _modo_masivo = None               # (elección, cuántos faltaban) cuando se preguntó cómo analizar miles de temas

    def _analisis_en_curso(self):
        return self._analizador_bib is not None and self._analizador_bib.isRunning()

    def _iniciar_analisis(self, rutas, forzar, fin, texto, masivo=False):
        self._dlg_analisis = None
        if masivo:
            n = AnalizadorBiblioteca._limitar_por_ram(max(1, min(12, os.cpu_count() or 2)))
            base = 0 if forzar else max(0, len(self.biblioteca.todas()) - len(rutas))     # los ya analizados cuentan en el avance
            if self._dlg_prep is not None:                    # la ventanita ya estaba abierta (botón Acelerar): se la completa con los datos reales
                dlg = self._dlg_analisis = self._dlg_prep
                self._dlg_prep = None
                for sen in (dlg.cancelar, dlg.segundo_plano):
                    try:
                        sen.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                dlg.btn.setEnabled(True); dlg.btn_fondo.setEnabled(True)
                dlg.reconfigurar(len(rutas), n, base)
            else:
                dlg = self._dlg_analisis = DialogoAnalisis(None, len(rutas), n, permitir_fondo=not forzar, base=base)
                dlg.show(); dlg.raise_(); dlg.activateWindow()
                self._oculta_por_analisis = self.isVisible()
                self.hide()                                   # el reproductor se oculta mientras se analiza a máxima velocidad
            QApplication.processEvents()                      # que se vea la ventana antes del trabajo pesado de marcar la lista
        self.biblioteca.marcar(rutas)
        a = AnalizadorBiblioteca(rutas, forzar=forzar, modo_max=masivo)
        _mantener_vivo(a)
        self._lote_listos = []
        if masivo:
            a.progreso.connect(dlg.poner)
            a.en_curso.connect(dlg.poner_actual)
            dlg.cancelar.connect(lambda: setattr(a, "cancelar", True))
            dlg.segundo_plano.connect(lambda: self._pasar_a_modo("suave"))
        elif not forzar:
            self.btn_reanalizar.setText("⚡ Acelerar")
            self.btn_reanalizar.setToolTip("Pasa el análisis a máxima velocidad: aparece una ventanita con el avance y el reproductor se oculta hasta que termine")
        a.progreso.connect(lambda i, t: self._set_status(f"{texto}: {i}/{t}"))
        a.tema_fallo.connect(self._tema_danado_analisis)
        a.tema_listo.connect(self._tema_analizado)        # los temas terminados se acumulan y la lista se actualiza por lotes
        a.terminado.connect(fin)
        self._analizador_bib = a
        self._actualizar_titulo_lib()
        a.start()

    def _ms_lote(self):
        """Cada cuánto se actualiza la lista con los temas ya analizados: casi enseguida si la lista es chica; con miles de
        temas, cada actualización recorre toda la lista (0,2-0,4 s), así que se hace cada pocos segundos."""
        n = self.biblioteca.count()
        if n < 800:
            return 600
        try:
            from setup import hilos_analisis as _ha
            if _ha._SONANDO:
                return 15000                  # con música sonando, el repintado de la lista (miles de filas) se hace pocas veces
        except Exception:
            pass
        return min(4000, 600 + n // 3)

    def _tema_analizado(self, ruta):
        """Un tema terminó su análisis: se anota y se actualiza la lista en el próximo lote (no uno por uno)."""
        if not hasattr(self, "_lote_listos") or self._lote_listos is None:
            self._lote_listos = []
        self._lote_listos.append(ruta)
        if getattr(self, "_dlg_analisis", None) is not None:
            return                                            # análisis masivo: la lista se actualiza una sola vez, al final
        t = getattr(self, "_t_lote", None)
        if t is None:
            t = self._t_lote = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._vaciar_lote)
        if not t.isActive():
            t.start(self._ms_lote())

    def _vaciar_lote(self):
        """Actualiza la lista de una vez con todos los temas analizados desde el último lote."""
        rutas, self._lote_listos = (getattr(self, "_lote_listos", None) or []), []
        if not rutas:
            return
        self.biblioteca.quitar_de_cola_lote(rutas)
        self._actualizar_titulo_lib()                                    # Biblioteca (5/17)
        # la lista se va ordenando a medida que se analiza: con miles de temas, como mucho una vez cada 20 s (reordenar cuesta)
        n = self.biblioteca.count()
        if n < 800:
            ms_ord = 600
        else:
            try:
                from setup import hilos_analisis as _ha
                ms_ord = 60000 if _ha._SONANDO else 20000      # reordenar miles de temas cuesta: menos seguido si hay música sonando
            except Exception:
                ms_ord = 20000
        self._reordenar_pronto(ms_ord, fijo=True)

    # ---------- análisis en un programa aparte (miles de temas) ----------
    def _tema_danado_analisis(self, ruta, motivo):
        """El análisis no pudo leer este archivo: va al historial de temas dañados."""
        self._anotar_danado(ruta, motivo)

    def _aparte_vivo(self):
        p = self._proc_aparte
        return (p is not None and p.poll() is None) or aparte_activo()

    def _preguntar_modo_masivo(self, n):
        """Con miles de temas sin analizar: 'aparte' (programa propio, recomendado), 'aqui' (en segundo plano, lento) o 'no'."""
        cuadro = QMessageBox(self)
        cuadro.setIcon(QMessageBox.Icon.Question)
        cuadro.setWindowTitle("AutoMix — muchos temas sin analizar")
        cuadro.setText(f"Hay {n:,} temas sin analizar.".replace(",", "."))
        cuadro.setInformativeText("Con tantos conviene analizarlos en un programa aparte: tiene su propia ventana, usa todos los núcleos y deja "
                                  "los resultados en la carpeta cache. El reproductor sigue funcionando y la lista se va actualizando sola.")
        b_aparte = cuadro.addButton("🔬 Analizar en programa aparte (recomendado)", QMessageBox.ButtonRole.AcceptRole)
        b_aqui = cuadro.addButton("Analizar acá, en segundo plano (lento)", QMessageBox.ButtonRole.ActionRole)
        b_no = cuadro.addButton("Ahora no", QMessageBox.ButtonRole.RejectRole)
        cuadro.setDefaultButton(b_aparte)
        cuadro.exec()
        clic = cuadro.clickedButton()
        return "aparte" if clic is b_aparte else ("aqui" if clic is b_aqui else "no")

    def _lanzar_aparte(self, rutas, forzar=False):
        """Abre el analizador aparte con estos temas y se queda mirando el índice para ir actualizando la lista."""
        sonando = bool(self.deck_a.playing or self.deck_b.playing)
        try:
            self._proc_aparte = lanzar_aparte(rutas, suave=sonando, forzar=forzar)
        except OSError as e:
            self._set_status(f"❌ No se pudo abrir el analizador aparte: {e}")
            return
        self._aparte_total = len(rutas)
        bib = self.biblioteca
        bib._bombeo = True                                    # (marcar recorre toda la lista: que no quede sin responder)
        try:
            bib.marcar(rutas)
        finally:
            bib._bombeo = False
        self._set_status(f"🔬 Analizando {len(rutas):,} temas en el programa aparte".replace(",", ".") + (" (modo suave: está sonando música)" if sonando else ""))
        self._seguir_aparte()

    def _seguir_aparte(self):
        """Arranca (si no corre) el reloj que revisa el índice cada pocos segundos mientras el programa aparte analiza."""
        bib = self.biblioteca
        if not self._aparte_aviso_marcado():
            pend = self._pendientes()                         # el programa aparte lo abrió otra sesión: se marcan igual como pendientes
            bib._bombeo = True
            try:
                bib.marcar(pend)
            finally:
                bib._bombeo = False
            self._aparte_total = max(self._aparte_total, len(pend))
        if self._t_aparte is None:
            self._t_aparte = QTimer(self)
            self._t_aparte.setInterval(10000)
            self._t_aparte.timeout.connect(self._vigilar_aparte)
        if not self._t_aparte.isActive():
            self._t_aparte.start()

    def _aparte_aviso_marcado(self):
        return bool(self.biblioteca._cola)

    def _vigilar_aparte(self):
        """Cada 10 s: trae del índice los temas ya analizados (solo esas filas) y avisa el avance. Cuando el programa aparte termina, cierra."""
        try:
            self.biblioteca.sincronizar_indice()
        except Exception:
            import traceback; traceback.print_exc()
        if self._aparte_vivo():
            falta = len(self.biblioteca._cola)
            total = max(self._aparte_total, falta, 1)
            self._set_status(f"🔬 Analizando en el programa aparte: {total - falta:,} / {total:,}".replace(",", "."))
            return
        if self._t_aparte is not None:
            self._t_aparte.stop()
        self._fin_aparte()

    def _fin_aparte(self):
        """El programa aparte terminó (o se cerró): se trae lo último, se ordena la lista y se anota qué quedó sin analizar."""
        p, self._proc_aparte = self._proc_aparte, None
        rc = p.poll() if p is not None else None
        bib = self.biblioteca
        bib._bombeo = True                                    # (recorridos largos: que la ventana atienda al sistema)
        try:
            for _ in range(40):                               # lo último que quedó en el índice
                if not bib.sincronizar_indice():
                    break
            bib.marcar(())
            try:
                self._ordenar_lista()
            except Exception:
                import traceback; traceback.print_exc()
        finally:
            bib._bombeo = False
        if self._fallidos is None:
            self._fallidos = set()
        pend = self._pendientes()
        for r in pend:                                        # lo que quedó sin analizar no se reintenta solo (Re-analizar lo retoma)
            self._fallidos.add(bib._clave(r))
        self._modo_masivo = ("no", len(pend))
        self._actualizar_titulo_lib()
        if rc not in (None, 0) and len(pend) >= self._aparte_total > 0:
            self._set_status(f"❌ El programa aparte se cerró con error (código {rc}). Mirá cache/consola.log")
        elif pend:
            self._set_status(f"⏹ Análisis aparte terminado: quedan {len(pend):,} temas sin analizar (botón Re-analizar para retomar)".replace(",", "."))
        else:
            self._set_status("✅ Análisis aparte terminado: todo analizado")

    def _pendientes(self):
        if self._fallidos is None:
            self._fallidos = set()
        from setup.danados import _clave as _clave_danado
        danados = getattr(self, "_danados", None) or set()       # los temas ya anotados como dañados (⚠) no se vuelven a intentar en cada arranque
        return [r for r in self.biblioteca.temas_sin_analisis()
                if self.biblioteca._clave(r) not in self._fallidos and os.path.isfile(r) and _clave_danado(r) not in danados]

    def _cerrar_dlg(self, dlg):
        """Cierra la ventanita del análisis y devuelve el reproductor. Primero el reproductor: si no, al cerrarse la
        última ventana visible el programa entero se cerraría."""
        try:
            if self._oculta_por_analisis or not self.isVisible():
                self.show()
            self._oculta_por_analisis = False
            self.raise_(); self.activateWindow()
        finally:
            dlg.btn.setEnabled(False); dlg.btn_fondo.setEnabled(False)
            dlg.close(); dlg.deleteLater()

    def _pasar_a_modo(self, modo):
        """Con un análisis en curso: lo detiene a propósito y lo reinicia en 'max' (ventanita, a toda velocidad) o 'suave' (segundo plano)."""
        a = self._analizador_bib
        if a is None or not a.isRunning():
            return
        self._modo_siguiente = modo
        a.cancelar = True

    def _mostrar_prep(self):
        """Botón ⚡ Acelerar con un análisis en curso: la ventanita aparece YA (el análisis anterior puede tardar en cerrarse: termina el tema
        que tiene entre manos) y se completa cuando arranca el análisis rápido."""
        if self._dlg_prep is not None:
            return
        dlg = self._dlg_prep = DialogoAnalisis(None, max(1, len(self.biblioteca._cola)), 0, permitir_fondo=True, base=0)
        dlg.lbl.setText("( … )")
        dlg.lbl_vel.setText("⏳ Terminando el tema que se estaba analizando…")
        dlg.cancelar.connect(lambda: self._prep_elegido("cancelar"))
        dlg.segundo_plano.connect(lambda: self._prep_elegido("suave"))
        dlg.show(); dlg.raise_(); dlg.activateWindow()
        self._oculta_por_analisis = self.isVisible()
        self.hide()
        QApplication.processEvents()

    def _prep_elegido(self, que):
        """Se tocó 'Detener' o 'Seguir en segundo plano' en la ventanita mientras se preparaba el análisis rápido."""
        self._prep_modo = que
        dlg, self._dlg_prep = self._dlg_prep, None
        if dlg is not None:
            self._cerrar_dlg(dlg)

    def _reiniciar_analisis(self, modo, intentos=0):
        if self._analisis_en_curso():                         # esperar a que el hilo anterior termine de cerrarse (puede tardar: termina el tema que analiza)
            if self._prep_modo is None and self._dlg_prep is None and intentos >= 75:
                return                                        # sin ventanita de espera y pasó mucho: se deja (como antes)
            QTimer.singleShot(200, lambda: self._reiniciar_analisis(modo, intentos + 1))
            return
        elegido, self._prep_modo = self._prep_modo, None
        if elegido == "cancelar":
            return
        if elegido == "suave":
            modo = "suave"
        self._auto_analizar(modo)
        if self._dlg_prep is not None:                        # no hubo nada que analizar a máxima velocidad: se cierra la ventanita y vuelve el reproductor
            dlg, self._dlg_prep = self._dlg_prep, None
            self._cerrar_dlg(dlg)

    def _auto_analizar(self, modo=None):
        """Los temas nuevos de la lista se analizan solos (se ven con ⏳ hasta que terminan).
        modo: None = automático (ventanita si son más de UMBRAL_MASIVO y no suena nada), 'max' = a máxima velocidad, 'suave' = segundo plano."""
        if not isinstance(modo, str):
            modo = None
        if self.biblioteca._cargando_lib:                    # la lista todavía se está cargando al abrir: cuando termine, avisa sola
            return
        if self._analisis_en_curso():
            return
        if self._aparte_vivo():                              # ya hay un analizador aparte trabajando
            self._seguir_aparte()
            return
        faltan = self._pendientes()
        if not faltan:
            self._ordenar_al_terminar = False
            return
        if len(faltan) > UMBRAL_APARTE and modo != "suave":      # miles de temas: en el programa aparte
            if modo == "max":
                self._lanzar_aparte(faltan)
                return
            previo = self._modo_masivo
            if previo is None or len(faltan) > previo[1] + UMBRAL_MASIVO:      # se pregunta una vez (y de nuevo si se agregaron muchos más)
                self._modo_masivo = previo = (self._preguntar_modo_masivo(len(faltan)), len(faltan))
            if previo[0] == "aparte":
                self._lanzar_aparte(faltan)
                return
            if previo[0] == "no":
                self._set_status(f"⏳ {len(faltan):,} temas sin analizar (botón Re-analizar para analizarlos)".replace(",", "."))
                return
            modo = "suave"                                   # 'aqui': en este programa, en segundo plano
        self._set_status(f"⏳ Analizando {len(faltan)} temas (BPM)…")
        sonando = bool(self.deck_a.playing or self.deck_b.playing)
        if modo == "suave":
            masivo = False
        elif modo == "max":
            masivo = not sonando
        else:
            masivo = len(faltan) > UMBRAL_MASIVO and not sonando     # (con música sonando se analiza en modo suave)
        self._iniciar_analisis(faltan, False, self._fin_analisis, "⏳ Analizando BPM", masivo=masivo)

    def _fin_analisis(self):
        dlg = getattr(self, "_dlg_analisis", None)
        modo, self._modo_siguiente = self._modo_siguiente, None
        self.btn_reanalizar.setText("🔄 Re-analizar")
        self.btn_reanalizar.setToolTip("Si hay temas sin analizar, los analiza a máxima velocidad (ventanita con el avance, el reproductor se oculta); si no hay pendientes no hace nada (Ctrl + clic = volver a analizar TODO desde cero)")
        if dlg is not None:
            dlg.fase("Actualizando la lista…" if modo else "Actualizando y ordenando la lista…")
            QApplication.processEvents()
            self._dlg_analisis = None                         # a partir de acá la lista se actualiza normal
            for nom in ("_t_lote", "_t_reordenar"):           # nada de lotes ni reordenados programados mientras se hace el cierre
                t = getattr(self, nom, None)
                if t is not None:
                    t.stop()
            self.biblioteca._bombeo = True                    # con miles de temas cada recorrido tarda segundos: la lista atiende al sistema cada ~0,1 s (si no, Windows la marca "No responde")
            try:
                self._vaciar_lote()                           # los últimos temas analizados
                self.biblioteca.marcar(())
                if not modo:
                    dlg.fase("Ordenando la lista…")
                    QApplication.processEvents()
                    try:
                        self._ordenar_lista()                 # orden final, con la ventana de progreso todavía a la vista
                    except Exception:
                        import traceback; traceback.print_exc()
                    self._ordenar_al_terminar = False
            finally:
                self.biblioteca._bombeo = False
                for nom in ("_t_lote", "_t_reordenar"):
                    t = getattr(self, nom, None)
                    if t is not None:
                        t.stop()
            self._cerrar_dlg(dlg)
        else:
            self._vaciar_lote()                               # los últimos temas analizados
            self.biblioteca.marcar(())
            if not modo:
                self._reordenar_pronto(300)                   # orden final con todo lo analizado
        self._actualizar_titulo_lib()
        if modo:                                              # cancelado a propósito para cambiar de modo: se sigue con lo que falta
            self._set_status("⚡ Analizando a máxima velocidad…" if modo == "max" else "⏬ Analizando en segundo plano…")
            self._reiniciar_analisis(modo)
            return
        if self._fallidos is None:
            self._fallidos = set()
        hechos = {self.biblioteca._clave(r) for r in (getattr(getattr(self, "_analizador_bib", None), "rutas", None) or ())}
        for r in self.biblioteca.temas_sin_analisis():       # los que se intentaron y no se pudieron analizar no se reintentan en bucle
            k = self.biblioteca._clave(r)                    # (los agregados mientras se analizaba NO: esos todavía no se probaron)
            if k in hechos:
                self._fallidos.add(k)
        if self._ordenar_al_terminar:
            self._ordenar_al_terminar = False
            self._ordenar_lista()
            self._set_status("✅ Biblioteca ordenada")
        else:
            self._set_status("✅ Lista analizada")
        QTimer.singleShot(300, self._auto_analizar)           # por si se agregaron temas mientras tanto

    _modo_orden = "bpm"               # 'bpm' (orden de siempre) o 'estilo' (🎵 Ordenar por estilo: no se reordena sola al analizar)
    _estilo_corriendo = False
    _estilo_conectado = False

    def _ordenar_por_estilo(self):
        """🎵 Ordena la lista del tema más parecido al menos parecido (sin carpetas), partiendo del tema que suena.
        Lee los análisis en un hilo (la ventana sigue respondiendo)."""
        if self._estilo_corriendo:
            return
        bib = self.biblioteca
        rutas = bib.todas()
        if len(rutas) < 3:
            self._set_status("🎵 Hacen falta al menos 3 temas en la lista para ordenarlos por estilo")
            return
        prim = self._deck_primario()
        inicio = prim.path if (prim is not None and prim.path) else None
        if not inicio:
            sel = [x for x in bib.selectedItems() if not bib._es_cab(x)]
            inicio = sel[0].data(Qt.ItemDataRole.UserRole) if sel else None
        if not self._estilo_conectado:
            self._estilo_conectado = True
            bib.estilo_listo.connect(self._estilo_terminado)
            bib.estilo_progreso.connect(lambda i, n: self._set_status(f"⏳ Leyendo el análisis de los temas… {i} / {n}"))
        self._estilo_corriendo = True
        self.btn_estilo.setEnabled(False)
        self._set_status(f"⏳ Leyendo el análisis de {len(rutas)} temas…")

        def _run():
            try:
                res = estilo.ordenar(rutas, inicio, lambda i, n: bib.estilo_progreso.emit(i, n))
            except Exception as e:
                import traceback; traceback.print_exc()
                res = e
            bib.estilo_listo.emit(res)
        threading.Thread(target=_run, daemon=True, name="orden-estilo").start()

    def _estilo_terminado(self, res):
        self._estilo_corriendo = False
        self.btn_estilo.setEnabled(True)
        if isinstance(res, Exception):
            self._set_status(f"⚠️ No se pudo ordenar por estilo ({type(res).__name__}: {res})")
            return
        orden, con = res
        if con < 2:
            self._set_status("🎵 Casi ningún tema de la lista tiene análisis todavía: analizalos primero (🔄 Re-analizar)")
            return
        self._modo_orden = "estilo"                       # (antes de apagar «Por carpetas»: a partir de acá el orden de la lista no se pisa solo)
        guardar_config(lib_orden="estilo")
        if self.btn_agrupar.isChecked():                  # lista plana: sin carpetas, todos los temas juntos
            self.btn_agrupar.blockSignals(True); self.btn_agrupar.setChecked(False); self.btn_agrupar.blockSignals(False)
            self._alternar_agrupada(False)
        self.biblioteca.aplicar_orden(orden)
        sin = len(orden) - con
        self._set_status(f"🎵 Lista ordenada por estilo: del más parecido al menos parecido ({con} temas)"
                         + (f" · {sin} sin análisis quedaron al final" if sin else ""))

    def _destildar_tocados(self):
        """Destilda los temas que se tildaron solos por haber sonado (los que se tildaron a mano se respetan)."""
        n, a_mano = self.biblioteca.destildar_tocados()
        if n:
            self._set_status(f"⬜ {n} temas destildados" + (f" · los {a_mano} que tildaste a mano se quedan tildados" if a_mano else ""))
        else:
            self._set_status("⬜ No hay temas tildados por haber sonado" + (f" (los {a_mano} tildados a mano no se tocan)" if a_mano else ""))

    def _ordenar_lista(self):
        """Con 🎼 Armónico y/o 🥁 En frase la lista se ordena como cadena de mezcla (empezando por el tema que
        suena); sin ninguna, por BPM de menor a mayor. Con 🎵 Ordenar por estilo activo, no se toca."""
        if self._modo_orden == "estilo":
            return
        arm, fra = self.chk_armonico.isChecked(), self.chk_frases.isChecked()
        if not (arm or fra):
            self.biblioteca.ordenar_por_bpm()
            return
        prim = self._deck_primario()
        inicio = prim.path if (prim is not None and prim.path) else None
        self.biblioteca.ordenar_inteligente(arm, fra, inicio)

    def _reordenar_pronto(self, ms=600, fijo=False):
        """Reordena la lista (con retardo, para agrupar varios cambios) a medida que se analizan los temas.
        Con fijo=True, si ya hay un reordenado programado no se reprograma (así corre cada `ms` aunque lleguen cambios sin parar)."""
        t = getattr(self, "_t_reordenar", None)
        if t is None:
            t = self._t_reordenar = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._reordenar_ahora)
        if fijo and t.isActive():
            return
        t.start(ms)

    def _reordenar_ahora(self):
        if self.biblioteca._bombeo:                                    # ya se está ordenando (cierre del análisis masivo): no se anida otro
            self._reordenar_pronto(500)
            return
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:     # no mover la lista mientras se arrastra un tema
            self._reordenar_pronto(500)
            return
        if self.mix_timer.isActive() or self.mix_pausa_timer.isActive():     # durante una mezcla no se reordena la lista (con miles de temas traba la ventana)
            self._reordenar_pronto(2500)
            return
        try:
            self._ordenar_lista()
        except Exception:
            import traceback; traceback.print_exc()

    def _alternar_agrupada(self, v):
        self.biblioteca.set_agrupado(bool(v))
        if v:
            if self._modo_orden != "bpm":
                self._modo_orden = "bpm"                      # por carpetas: dentro de cada una, el orden de siempre
                guardar_config(lib_orden="bpm")
            self._ordenar_lista()                             # dentro de cada carpeta, el orden de siempre (por BPM)
        guardar_config(lib_agrupada=bool(v))
        self._set_status("📂 Lista separada por carpetas (clic en un título para cerrarlo o abrirlo)" if v else "📃 Lista plana")

    # ---------- botón << (contraer / expandir todas las carpetas) ----------
    def _toggle_plegar_carpetas(self):
        """Botón << del buscador: contrae / expande TODAS las carpetas. Si la vista por carpetas está apagada,
        la activa primero y deja todo contraído."""
        bib = self.biblioteca
        if not bib.agrupado:
            self.btn_agrupar.setChecked(True)      # activa «Por carpetas» (dispara _alternar_agrupada)
            bib.plegar_todas()
            self.btn_plegar.setText(">>")
            self.btn_plegar.setToolTip("Expandir todas las carpetas")
            self._set_status("📂 Carpetas contraídas")
            return
        if bib.todas_plegadas():
            bib.desplegar_todas()
            self.btn_plegar.setText("<<")
            self.btn_plegar.setToolTip("Contraer todas las carpetas")
            self._set_status("📂 Carpetas expandidas")
        else:
            bib.plegar_todas()
            self.btn_plegar.setText(">>")
            self.btn_plegar.setToolTip("Expandir todas las carpetas")
            self._set_status("📂 Carpetas contraídas")

    def _random_cambio(self, on):
        """Botón ➡ Normal / 🎲 Random: cómo se elige el tema que sigue."""
        self.btn_random.setText("🎲 Random" if on else "➡ Normal")
        self._tocados_rnd = set()                                   # al activarlo empieza una vuelta nueva
        self._set_status("🎲 Modo Random: el que sigue es uno al azar, analizado y de BPM parecido al que suena" if on else "➡ Modo normal: sigue el orden de la lista")

    def _ordenar_biblioteca_bpm(self):
        if self._modo_orden != "bpm":
            self._modo_orden = "bpm"                          # el clic explícito vuelve al orden por BPM
            guardar_config(lib_orden="bpm")
        self._ordenar_lista()                                 # ya: primero los que tienen BPM conocido
        self._fallidos = set()                                # un clic explícito vuelve a intentar los que fallaron
        faltan = self._pendientes()
        if not faltan:
            self._set_status("✅ Biblioteca ordenada por BPM")
            return
        self._ordenar_al_terminar = True
        if self._analisis_en_curso():
            self._set_status("⏳ Analizando la lista… se ordena sola al terminar")
            return
        self._set_status(f"⏳ Analizando {len(faltan)} temas (BPM)…")
        self._iniciar_analisis(faltan, False, self._fin_analisis, "⏳ Analizando BPM")

    def _reanalizar_biblioteca(self):
        sonando = bool(self.deck_a.playing or self.deck_b.playing)
        if self._aparte_vivo():
            self._set_status("🔬 Ya se está analizando en el programa aparte (ventana AutoMix — Analizador aparte)")
            self._seguir_aparte()
            return
        if self.biblioteca._cargando_lib or getattr(self.biblioteca, "_carga", None) is not None:
            self._set_status("⏳ La biblioteca todavía se está cargando: cuando termine, los temas sin analizar se analizan solos (a máxima velocidad si no suena nada)")
            return
        if self._analizador_bib is not None and self._analizador_bib.isRunning():
            a = self._analizador_bib
            if a.forzar:                                          # re-análisis completo en curso: segundo clic = cancelar
                a.cancelar = True
                self._set_status("⏹ Cancelando re-análisis…")
            elif getattr(a, "modo_max", False):
                pass                                              # ya va a máxima velocidad (la ventanita está a la vista)
            elif sonando:
                self._set_status("🔇 Pausá la música para analizar a máxima velocidad (el reproductor se oculta)")
            else:
                self._set_status("⚡ Pasando a máxima velocidad…")
                self._mostrar_prep()                              # la ventanita aparece al instante
                self._pasar_a_modo("max")                         # análisis normal en curso → a toda velocidad con la ventanita
            return
        self._fallidos = set()                                    # lo que quedó sin analizar (cancelado, etc.) se vuelve a intentar
        desde_cero = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)      # Ctrl + clic = volver a analizar TODO
        pend = self._pendientes()
        if pend and not desde_cero and len(pend) > UMBRAL_APARTE:      # miles de temas: en el programa aparte
            self._lanzar_aparte(pend)
            return
        if pend and not desde_cero:
            if sonando:
                self._set_status("🔇 Pausá la música para analizar a máxima velocidad (el reproductor se oculta)")
                return
            self._auto_analizar("max")
            return
        rutas = self.biblioteca.todas()
        if not rutas:
            self._set_status("La lista está vacía")
            return
        if desde_cero and len(rutas) > UMBRAL_APARTE:
            self._lanzar_aparte(rutas, forzar=True)           # volver a analizar TODO desde cero, en el programa aparte
            return
        if not desde_cero:
            self._set_status("✅ Todo está analizado (Ctrl + clic en el botón para volver a analizar todo desde cero)")
            return
        masivo = len(rutas) > UMBRAL_MASIVO and not sonando
        if masivo:
            self._set_status(f"🔄 Re-analizando {len(rutas)} temas a máxima velocidad…")
        else:
            self._set_status(f"🔄 Re-analizando {len(rutas)} temas… (clic otra vez en el botón para cancelar)")
            self.btn_reanalizar.setText("⏹ Cancelar")
        self._iniciar_analisis(rutas, True, self._fin_reanalisis, "🔄 Re-analizando BPM", masivo=masivo)

    def _fin_reanalisis(self):
        dlg = getattr(self, "_dlg_analisis", None)
        if dlg is not None:
            dlg.fase("Actualizando y ordenando la lista…")
            QApplication.processEvents()
            self._dlg_analisis = None
            self._vaciar_lote()
        self.biblioteca.marcar(())
        if dlg is not None:
            try:
                self._ordenar_lista()
            except Exception:
                import traceback; traceback.print_exc()
            self._cerrar_dlg(dlg)
        cancelado = bool(self._analizador_bib and self._analizador_bib.cancelar)
        self.btn_reanalizar.setText("🔄 Re-analizar")
        self._set_status("⏹ Re-análisis cancelado" if cancelado else
                         "✅ Lista re-analizada. Si un tema ya estaba cargado en un deck, volvé a cargarlo para usar el BPM nuevo")

    def _filtrar_biblioteca(self, txt):
        t = txt.strip().lower()
        for i in range(self.biblioteca.count()):
            it = self.biblioteca.item(i)
            if self.biblioteca._es_cab(it):
                continue
            it.setHidden(bool(t) and t not in it.text().lower())

    def _actualizar_titulo_lib(self):
        """Título de la lista: 'Biblioteca: ( analizados / total )'. El conteo se hace a lo sumo una vez por segundo."""
        ahora = time.monotonic()
        if ahora - getattr(self, "_t_titulo_lib", 0.0) < 1.0:
            if not getattr(self, "_titulo_lib_pendiente", False):          # se vuelve a pintar una sola vez cuando pase el segundo
                self._titulo_lib_pendiente = True
                QTimer.singleShot(1000, self._titulo_lib_diferido)
            return
        self._t_titulo_lib = ahora
        total = len(self.biblioteca.todas())
        try:
            hechos = self.biblioteca.contar_analizados()
        except Exception as e:
            log("biblioteca", f"no se pudo contar los analizados: {e}")
            hechos = None
        if hechos is None:                                                 # sin índice: se estima con la cola de pendientes
            pend = len(self.biblioteca._cola)
            hechos = total - pend if pend <= total else total
        hechos = min(hechos, total)
        self.lbl_lib.setText(f"📁 Biblioteca: ( {hechos} / {total} )")
        if hechos < total and self.biblioteca._cola:
            self.setWindowTitle(f"AutoMix 🎚️  —  🔬 Analizando {hechos:,} / {total:,}  ({100 * hechos // max(1, total)} %)".replace(",", "."))
        else:
            self.setWindowTitle("AutoMix 🎚️")

    def _titulo_lib_diferido(self):
        self._titulo_lib_pendiente = False
        self._actualizar_titulo_lib()

    def _biblioteca_cambio(self):
        self._actualizar_titulo_lib()
        self.biblioteca.guardar()
        if self.biblioteca.count() == 0:
            an = self._analizador_bib
            if an is not None and an.isRunning():
                an.cancelar = True                   # lista vaciada: no se sigue analizando temas que ya no están
            self._lote_listos = []
            self._vaciar_decks()

    def _vaciar_decks(self):
        """La lista quedó vacía: se descargan los reproductores que NO están sonando. El que está sonando sigue hasta que termine el tema
        (así hay tiempo de cargar carpetas nuevas sin que se corte la música); la mezcla en curso tampoco se toca."""
        a, b = self.deck_a, self.deck_b
        if a.audio is None and b.audio is None and not a.path and not b.path:
            return
        sonando = [d for d in (a, b) if d.playing and d.audio is not None]
        if sonando:
            log("programa", "lista vacía: sigue sonando el Deck " + " y ".join(d.name for d in sonando) + " hasta que termine el tema")
            for d in (a, b):
                if d not in sonando:
                    try:
                        d.descargar()
                    except Exception:
                        import traceback; traceback.print_exc()
            try:
                self.timeline.redraw()
            except Exception:
                pass
            self.biblioteca.pintar(a.path if a in sonando else None, b.path if b in sonando else None)
            self._set_status("🗑️ Lista vacía: " + " y ".join(f"el Deck {d.name}" for d in sonando) + " sigue sonando hasta que termine. Cargá las carpetas nuevas")
            return
        log("programa", "lista vacía: se descargan los dos decks")
        self.mix_timer.stop(); self.mix_pausa_timer.stop()
        try: self._nivel_restaurar()
        except Exception: pass
        self._plan = None
        self._frase_armada = False
        self._mix_esperando = False
        self._mix_auto = False
        self._target_rate_a = self._target_rate_b = None
        try:
            self._soltar_sync(None)
        except Exception:
            pass
        self.magnet_on = False
        a.descargar(); b.descargar()
        try:
            self.timeline.redraw()
        except Exception:
            import traceback; traceback.print_exc()
        self.biblioteca.pintar(None, None)
        self._set_status("🗑️ Lista vacía: reproductores descargados")

    def _restaurar_decks(self):
        """Al abrir: vuelven a quedar cargados los temas que tenían los decks al cerrar (si siguen existiendo y la lista no está vacía)."""
        if self.biblioteca.count() == 0:
            return
        cfg = leer_config().get("decks_cargados") or {}
        for d in (self.deck_a, self.deck_b):
            p = cfg.get(d.name)
            if p and os.path.isfile(p) and p.lower().endswith(SUPPORTED) and not d.path:
                log("programa", f"se recupera el Deck {d.name}: {os.path.basename(p)}")
                d.load_track(p)

    def _ubicar_en_lista(self, path):
        """Clic en el nombre del tema de un deck: lo busca en la biblioteca, lo selecciona y lo deja a la vista."""
        if not path:
            self._set_status("No hay ningún tema cargado en ese deck")
            return
        bib = self.biblioteca
        clave = bib._clave(path)
        for i in range(bib.count()):
            it = bib.item(i)
            if bib._es_cab(it):
                continue
            if bib._clave(it.data(Qt.ItemDataRole.UserRole)) == clave:
                if it.isHidden():                       # el filtro de búsqueda (o una carpeta cerrada) lo estaba ocultando
                    self.ed_filtro.clear()
                    if bib.agrupado:
                        bib._plegadas.discard(bib._carpeta(path)); bib._quitar_cab(); bib._poner_cab()
                        for j in range(bib.count()):
                            if not bib._es_cab(bib.item(j)) and bib._clave(bib.item(j).data(Qt.ItemDataRole.UserRole)) == clave:
                                it = bib.item(j); break
                bib.clearSelection()
                bib.setCurrentItem(it)
                it.setSelected(True)
                bib.scrollToItem(it, QAbstractItemView.ScrollHint.PositionAtCenter)
                self._set_status(f"📍 Ubicado en la lista: {os.path.basename(path)}")
                return
        self._set_status("Ese tema no está en la biblioteca")

    def _biblioteca_doble_clic(self, it):
        if self.biblioteca._es_cab(it):
            return
        p = it.data(Qt.ItemDataRole.UserRole)
        if not os.path.isfile(p): return
        a, b = self.deck_a, self.deck_b
        if a.playing and b.playing:
            self._set_status("Los dos decks están sonando.")
            return
        if not a.playing and not b.playing:
            # no suena nada: el tema elegido va al deck A y el que le sigue (según la lista / el modo armónico) al B
            a.load_track(p)
            try:
                ruta, motivo = self._siguiente_en_biblioteca(p)
            except Exception:
                import traceback; traceback.print_exc()
                ruta, motivo = None, "no se pudo elegir el siguiente"
            if ruta is not None and os.path.normcase(os.path.abspath(ruta)) != os.path.normcase(os.path.abspath(p)):
                b.load_track(ruta)
                k0, k1 = self._clave_de(a), self.biblioteca.info(ruta)[1]
                extra = f"  🎼 {k0}→{k1}" if (k0 and k1) else ""
                self._set_status(f"▶ Deck A: {os.path.basename(p)} · Deck B en espera: {os.path.basename(ruta)}{extra}")
            else:
                self._set_status(f"▶ Deck A: {os.path.basename(p)} · Deck B: {motivo or 'sin tema siguiente'}")
            return
        (b if a.playing else a).load_track(p)      # suena uno: el elegido va al deck que está en espera

    def _on_analysis(self, deck, res):
        self.timeline.redraw()
        try:
            self._apply_shared_bpm(self.crossfader.value() / 100.0)
        except Exception:
            import traceback; traceback.print_exc()
        try:                                  # el análisis llegó con el tema ya sonando: se empareja ahora mismo
            d = deck if isinstance(deck, Deck) else (self.deck_a if deck == "A" else self.deck_b)   # llega el nombre ("A"/"B")
            otro = self.deck_b if d is self.deck_a else self.deck_a
            if d.playing and otro.playing and not self.magnet_on and d.analysis and otro.analysis:
                self._auto_sync(d)
                self._alinear_ya(d)
        except Exception:
            import traceback; traceback.print_exc()
