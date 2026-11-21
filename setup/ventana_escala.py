"""AutoMix — tamaño de la ventana, escala de la interfaz y posición en pantalla."""
import re
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QGroupBox, QLayout, QScrollBar, QWidget
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QFont
from setup.ajustes import AJ, ANCHO_LISTA, ESC_COMPACTO, ESC_MIN, ESC_MIN_COMPACTO, ESC_PISO, FUENTE_BOOST, PANEL_CRUCE_MAX_W, PANEL_CRUCE_MIN_W
from setup.base import log


FRAC_LISTA_MAX = 0.25      # en ventana grande, la lista ocupa al menos este porcentaje del ancho (si el usuario no movió el divisor)


class EscalaMixin:
    """Tamaño de la ventana, escala de la interfaz y posición en pantalla (parte de la ventana principal)."""


    ESC_BASE_W = 1560.0
    ESC_BASE_H = 850.0

    def _registrar_escala(self):
        MAX = 16777215
        self._esc_fuentes = []
        for wd in [self] + self.findChildren(QWidget):
            if wd is self.biblioteca or self.biblioteca.isAncestorOf(wd):
                continue
            fb = wd.font()
            if fb.pointSizeF() > 0:
                self._esc_fuentes.append((wd, fb, fb.pointSizeF(), 0))
            elif fb.pixelSize() > 0:
                self._esc_fuentes.append((wd, fb, 0.0, fb.pixelSize()))
        self._esc_widgets = []
        for wd in self.findChildren(QWidget):
            if isinstance(wd, QScrollBar):
                continue
            p = wd.parentWidget()
            if isinstance(p, QAbstractScrollArea) and wd is p.viewport():
                continue
            mn, mx, ss = wd.minimumSize(), wd.maximumSize(), wd.styleSheet()
            if mn.width() or mn.height() or mx.width() < MAX or mx.height() < MAX or "font-size" in ss:
                self._esc_widgets.append((wd, mn.width(), mn.height(), mx.width(), mx.height(), ss))
        self._esc_layouts = []
        for lay in self.findChildren(QLayout):
            m = lay.contentsMargins()
            self._esc_layouts.append((lay, m.left(), m.top(), m.right(), m.bottom(), lay.spacing()))

    def _esc_min(self):
        """Escala mínima vigente: ESC_MIN en pantallas normales; más chica si hace falta para que entre en la pantalla."""
        return getattr(self, "_esc_min_val", ESC_MIN)

    def _preparar_medida(self):
        """Deja todos los widgets listos (letras y hojas de estilo ya aplicadas) para que minimumSizeHint() sea el real."""
        for wd in self.findChildren(QWidget):
            wd.ensurePolished()
            wd.updateGeometry()                 # cada widget guarda su tamaño mínimo: se descarta el viejo para medir el real
        for lay in self.findChildren(QLayout):
            lay.invalidate()
        if self.layout() is not None:
            self.layout().activate()

    def _buscar_escala(self, lim_w, lim_h, inicio=ESC_MIN):
        """Escala más grande (hasta `inicio`) a la que el tamaño mínimo de la ventana entra en lim_w x lim_h."""
        s = inicio
        for _ in range(25):
            self._aplicar_escala(s)
            self._preparar_medida()
            mh = self.minimumSizeHint()
            if mh.height() <= lim_h and mh.width() <= lim_w:
                return s, True
            if s <= ESC_PISO + 1e-9:
                log("ventana", f"aviso: aun con la escala mínima ({ESC_PISO}) la ventana ({mh.width()}x{mh.height()}) no entra en {lim_w}x{lim_h}")
                return s, False
            factor = min(lim_h / max(mh.height(), 1), lim_w / max(mh.width(), 1), 0.97)
            s = max(ESC_PISO, s * factor)
        return s, False

    def _calcular_esc_min(self):
        """Escala mínima = la más grande a la que el tamaño mínimo de la ventana entra en el área libre de la pantalla
        (sin la barra de tareas). Si hace falta bajar mucho (pantallas de 768 px de alto), primero se pasa al modo
        compacto (sin la tabla de bandas de la multibanda) en vez de achicar todo hasta dejar los controles cortados."""
        s = ESC_MIN
        try:
            ag = (self.screen() or QApplication.primaryScreen()).availableGeometry()
            fg, g = self.frameGeometry(), self.geometry()
            marco_w = max(fg.width() - g.width(), 16)          # borde + título de Windows (si todavía no se mostró, se estima)
            marco_h = max(fg.height() - g.height(), 40)
            lim_w = ag.width() - marco_w - 8                   # 4 px de separación a cada lado
            lim_h = ag.height() - marco_h - 8
            if hasattr(self, "_mb_compacto"):
                self._mb_compacto(False)
            s, _ = self._buscar_escala(lim_w, lim_h)
            if s < ESC_COMPACTO and hasattr(self, "_mb_compacto"):
                s_completa = s
                self._mb_compacto(True)
                s, _ = self._buscar_escala(lim_w, lim_h, ESC_MIN_COMPACTO)
                log("ventana", f"pantalla baja ({ag.width()}x{ag.height()} útiles): modo compacto (tabla de bandas oculta en automático) · escala mínima {s_completa:.2f} → {s:.2f}")
            elif s < ESC_MIN:
                log("ventana", f"pantalla chica ({ag.width()}x{ag.height()} útiles): escala mínima ajustada de {ESC_MIN} a {s:.2f}")
        except Exception as e:
            log("ventana", f"no se pudo ajustar la escala a la pantalla ({e})")
            s = ESC_MIN
        self._esc_min_val = s
        return s

    def _ajustar_escala_pantalla(self):
        """Calcula la escala mínima para ESTA pantalla, deja la ventana en su tamaño mínimo y fija la base de escala."""
        em = self._calcular_esc_min()
        self._aplicar_escala(em)
        self._preparar_medida()
        mh = self.minimumSizeHint()
        self.ESC_BASE_W = mh.width() / em * 1.05        # +5 % de holgura: el mínimo no crece exacto con la escala
        self.ESC_BASE_H = mh.height() / em * 1.05
        self.resize(mh)
        return em

    def changeEvent(self, e):
        super().changeEvent(e)
        try:
            if e.type() == QEvent.Type.WindowStateChange:
                antes_max = bool(e.oldState() & Qt.WindowState.WindowMaximized)
                if antes_max and not (self.windowState() & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowMinimized | Qt.WindowState.WindowFullScreen)):
                    QTimer.singleShot(0, self._volver_al_minimo)      # al des-maximizar: vuelve al tamaño mínimo de arranque
        except Exception:
            pass

    def _volver_al_minimo(self):
        if getattr(self, "_en_esquina", False):        # lo está acomodando el botón ↖: que no la centre en el medio del camino
            return
        log("ventana", "restaurada: vuelve al tamaño mínimo")
        self._aplicar_escala(self._esc_min())          # el mínimo depende de la escala: primero la de arranque
        self._ir_al_minimo()

    def _ir_al_minimo(self):
        self.resize(self.minimumSizeHint())
        self._escalar_segun_ventana()
        self._ajustar_a_pantalla()

    def _a_la_esquina(self):
        """Botón ↖: la ventana vuelve al tamaño mínimo de arranque y queda pegada a la esquina superior izquierda.
        Se repite en varias pasadas hasta que el tamaño y la escala quedan firmes (Windows aplica el cambio de
        tamaño y el re-escalado de las letras con retraso: con una sola pasada había que apretar dos veces)."""
        log("ventana", "botón ↖: tamaño mínimo en la esquina superior izquierda")
        self._gen_esquina = getattr(self, "_gen_esquina", 0) + 1
        gen = self._gen_esquina
        self._en_esquina = True
        QTimer.singleShot(1200, lambda: setattr(self, "_en_esquina", False))
        if self.isMaximized() or self.isFullScreen():
            # No se toca la geometría mientras está maximizada (Qt le quita la marca y Windows no: después no deja estirar los bordes).
            # Se restaura con la ventana invisible, se acomoda en la esquina y recién ahí se muestra: sin pasar a la vista por el centro.
            self.setWindowOpacity(0.0)
            self.showNormal()                          # el cambio de estado lo procesa Qt; después se acomoda
            self._oculta_esquina = True; self._firma_esquina = None
            QTimer.singleShot(1500, lambda: self.setWindowOpacity(1.0))     # (red de seguridad: nunca queda invisible)
        def _pasada(ultima=False):
            if gen != self._gen_esquina:               # se apretó otra vez: manda la pasada nueva
                return
            esc = self._esc_min()
            if ultima or abs(esc - self._esc_actual) > 1e-9:       # si la escala ya es la correcta no se re-aplica a todos los widgets (cada vez trababa la ventana y cortaba el audio)
                self._aplicar_escala(esc)
            QApplication.processEvents()
            self.layout().activate() if self.layout() is not None else None
            self.resize(self.minimumSizeHint())
            QApplication.processEvents()
            self._escalar_segun_ventana()
            self._ajustar_a_pantalla(esquina=True)
            if getattr(self, "_oculta_esquina", False):
                # La ventana reaparece recién cuando ya quedó firme (tamaño, posición y escala iguales en dos pasadas seguidas):
                # así no se ve el reescalado en pasos, solo aparece ya acomodada en la esquina.
                firma = (self.width(), self.height(), self.x(), self.y(), round(self._esc_actual, 3))
                if ultima or firma == self._firma_esquina:
                    self._oculta_esquina = False
                    self.setWindowOpacity(1.0)
                self._firma_esquina = firma
        for ms in (0, 120, 300, 600):
            QTimer.singleShot(ms, _pasada)
        QTimer.singleShot(1000, lambda: _pasada(True))

    def _ajustar_a_pantalla(self, esquina=False):
        """Al iniciar: que la ventana entre en la pantalla (barra de tareas incluida), centrada, con el
        contenido re-escalado y el timeline redibujado con su tamaño final. Con esquina=True queda arriba a la izquierda."""
        try:
            scr = self.screen() or QApplication.primaryScreen()
            ag = scr.availableGeometry()
            fg, g = self.frameGeometry(), self.geometry()
            marco_w, marco_h = fg.width() - g.width(), fg.height() - g.height()
            w = min(self.width(), ag.width() - marco_w)       # nunca más grande que la pantalla
            h = min(self.height(), ag.height() - marco_h)
            self.resize(w, h)
            if esquina:
                self.move(ag.left() + 4, ag.top() + 4)          # separada 4 px de los bordes
            else:
                self.move(ag.left() + (ag.width() - (w + marco_w)) // 2,
                          ag.top() + (ag.height() - (h + marco_h)) // 2)
        except Exception:
            pass
        self._escalar_segun_ventana()
        QTimer.singleShot(0, self._redibujar_timeline_inicial)
        QTimer.singleShot(250, self._redibujar_timeline_inicial)

    def _redibujar_timeline_inicial(self):
        try:
            self.timeline.redraw()
        except Exception:
            pass

    def _splitter_movido(self, *_):
        tot = sum(self.split.sizes())
        if tot > 0:
            self._ancho_lista_base = self.split.sizes()[0] / max(self._esc_actual, 1e-6)      # lo que acomodó el usuario, en unidades de escala 1.0

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "_esc_timer"):
            self._esc_timer.start(30)

    def _escalar_segun_ventana(self):
        s = min(self.width() / self.ESC_BASE_W, self.height() / self.ESC_BASE_H)
        s = max(self._esc_min(), min(1.6, int(s * 50) / 50.0))      # hacia abajo: nunca más grande que la ventana
        if abs(s - self._esc_actual) > 1e-9:
            self._aplicar_escala(s)
        tot = sum(self.split.sizes())
        if tot > 0:
            self._fijar_ancho_lista(tot)

    def _fijar_ancho_lista(self, tot=None):
        """La lista de temas tiene un ancho fijo (cambia con la escala); en ventanas grandes ocupa como mínimo un 25 % del ancho,
        y el espacio que sobra va al resto de la ventana (decks, paneles y timeline)."""
        tot = tot or sum(self.split.sizes())
        propio = getattr(self, "_ancho_lista_base", None)             # ancho que dejó el usuario arrastrando el divisor (si lo movió)
        base = propio or ANCHO_LISTA
        a = max(self.split.widget(0).minimumWidth(), int(round(base * self._esc_actual)))
        if not propio:
            a = max(a, int(round(tot * FRAC_LISTA_MAX)))               # ventana grande (maximizada): la lista crece un poco, con el resto de los controles repartidos
        self.split.setSizes([a, max(tot - a, 1)])

    def _aplicar_escala(self, s):
        MAX = 16777215
        self._esc_actual = s; AJ.escala = s
        for wd, fb, pt, px in self._esc_fuentes:
            try:
                nf = QFont(fb)
                if pt > 0: nf.setPointSizeF(max(7.0, pt * s * FUENTE_BOOST))
                else: nf.setPixelSize(max(9, int(round(px * s * FUENTE_BOOST))))
                wd.setFont(nf)
            except RuntimeError:
                pass
        r = lambda v: int(round(v * s))
        for wd, minw, minh, maxw, maxh, ss in self._esc_widgets:
            try:
                wd.setMinimumSize(r(minw), r(minh))
                wd.setMaximumSize(maxw if maxw >= MAX else r(maxw), maxh if maxh >= MAX else r(maxh))
                if "font-size" in ss:
                    nuevo = re.sub(r"font-size:\s*(\d+(?:\.\d+)?)px", lambda m: f"font-size:{max(9, int(round(float(m.group(1)) * s * FUENTE_BOOST)))}px", ss)
                    if nuevo != wd.styleSheet(): wd.setStyleSheet(nuevo)
            except RuntimeError:
                pass
        for lay, l, t, ri, b, sp in self._esc_layouts:
            try:
                lay.setContentsMargins(r(l), r(t), r(ri), r(b))
                if sp >= 0: lay.setSpacing(r(sp))
            except RuntimeError:
                pass
        pc = getattr(self, "panel_cruce", None)
        if pc is not None:                               # el panel "Forma de cruce A/B" no baja del ancho que piden sus controles
            try:
                if pc.layout() is not None:
                    pc.layout().invalidate(); pc.ensurePolished()
                ancho_min = max(r(PANEL_CRUCE_MIN_W), PANEL_CRUCE_MIN_W, pc.minimumSizeHint().width())
                pc.setMinimumWidth(ancho_min)
                pc.setMaximumWidth(max(r(PANEL_CRUCE_MAX_W), PANEL_CRUCE_MAX_W, ancho_min))
            except RuntimeError:
                pass
        for gb in self.findChildren(QGroupBox):          # los títulos de grupo se cortaban: recalcular su ancho con la letra nueva
            gb.setTitle(gb.title()); gb.updateGeometry(); gb.update()
