"""AutoMix — ⚙ Ajustes generales del cruce: todos los tiempos que arman el 'retardo' (la onda) del crossfader.
Lo que se mueve acá se aplica enseguida (en la próxima mezcla) y queda guardado."""
import math
import os
import time
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QMessageBox, QTabWidget, QComboBox, QDialog, QFrame, QScrollArea, QSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
                               QVBoxLayout, QWidget)
from setup import aprendizaje, cruce_auto
from setup.base import CACHE_DIR
from setup.estadisticas import EST
from setup.config import (DEF_FIN_A, DEF_GAMMA_A, DEF_PUNTO_A, DEF_PUNTO_B, DEF_PUNTO_S, DEF_RAMPA_B, guardar_config, leer_config,
                          perfil_cruce_ab, perfil_cruce_bandas)
from setup.tema import COLOR_DECK_A, COLOR_DECK_B, TEMA
from setup.widgets import qss_marco

CLAVE = "ajustes_cruce"

# (clave en cruce_auto, texto, mínimo, máximo, divisor, formato, ayuda)
# Ajustes que solo tienen efecto con ciertas estrategias (el resto valen siempre, también en Manual): se esconden cuando no sirven
SOLO_ESTRATEGIA = {"AUTO_OPTIMIZA": 2, "AUTO_ENTRADA_ESTRUCTURA": 2, "PRO_COMPASES": 1,
                   "MIN_DUR": 0, "DUR_IDEAL": 0, "MAX_DUR": 0, "ENT_MIN": 0, "ENT_MAX": 0, "RB_MIN": 0}


def _estrategia_activa():
    e = cruce_auto.ESTRATEGIA
    return 2 if e >= 1.5 else (1 if e >= 0.5 else 0)


GRUPOS = [
    ("🎛 Estrategia del cruce Auto", [
        ("ESTRATEGIA", "Estrategia (0 clásica · 1 Pro · 2 como mi manual)", 0, 2, 1, "{:.0f}",
         "2 = el Auto parte de su propia forma de cruce (panel de arriba: forma de A/B, pausa y tiempo total): el cruce dura exactamente el «Tiempo total» que pongas\ny el Auto ajusta por su cuenta la línea de compás donde pasa el bajo.\n1 = Pro: mezcla larga en frase (B entra sin graves, el bajo cambia a la mitad, A se va después), sin pausa ni frenado.\n0 = la clásica (la diseña el programa para cada par)."),
        ("NIVEL_LUFS", "Parejar el volumen de cada tema con su sonoridad real (tipo LUFS)", 0, 1, 1, "{:.0f}",
         "1 = al cargar un tema se mide su sonoridad con el filtro K de la norma BS.1770 y compuertas (ignora silencios, intros calladas y tramos muy bajos) y se le\n"
         "ajusta la ganancia para que todos suenen igual de fuertes. 0 = el método de antes (RMS promedio, que sube de más a los temas con intro callada).\n"
         "Vale para los temas que cargues desde ahora (los que ya están cargados siguen como estaban)."),
        ("AUTO_NIVELA", "Al cruzar, el tema que entra arranca al mismo nivel que el que sale", 0, 1, 1, "{:.0f}",
         "1 = en cada cruce del Auto se compara el nivel de los dos temas en el tramo donde suenan juntos (según su energía) y el que entra arranca con hasta ±3 dB\n"
         "de corrección para igualarlo; esa corrección se va soltando hasta 0 dB cuando termina el cruce. Evita el escalón de volumen al entrar. 0 = no corrige."),
        ("AUTO_ECO", "Temas que no combinan: el que sale se cierra en filtro y se disuelve en un eco", 0, 1, 1, "{:.0f}",
         "1 = cuando los dos temas no combinan (BPM muy distintos o tonalidades que chocan) el que sale va perdiendo agudos a medida que baja su volumen y deja un eco al compás\n"
         "que se apaga solo, así el que entra aparece sobre esa cola y el cambio se disimula. Con temas que combinan no se usa. 0 = sin efecto."),
        ("AUTO_HPF", "El tema que sale va perdiendo sus graves antes del traspaso del bajo (filtro pasa-altos)", 0, 1, 1, "{:.0f}",
         "1 = en los cruces del Auto por bandas, en el tramo anterior al traspaso del bajo los graves del tema que sale se van cerrando de a poco (como un filtro pasa-altos\n"
         "que sube, hasta unos -12 dB), así cuando el bajo pasa al tema que entra no se nota un hueco brusco y el bombo nuevo respira sin competir. 0 = el bajo se traspasa de golpe, como antes."),
        ("AUTO_TONO", "Si las tonalidades chocan, subir o bajar un semitono al tema que entra", 0, 1, 1, "{:.0f}",
         "1 = si los dos temas están en tonalidades que chocan (según la rueda de Camelot) y moviendo UN semitono al que entra quedan compatibles, el Auto lo hace: el tema entra con otro tono\n"
         "pero el mismo tempo (puede sonar apenas distinto al original, como cuando un DJ sube o baja el tono). Se queda así hasta que ese deck cambia de tema. Solo si la tonalidad detectada es confiable. 0 = nunca toca el tono."),
        ("AUTO_TEMPO_OCULTO", "El cambio de tempo entre los dos temas se hace donde suenan juntos", 0, 1, 1, "{:.0f}",
         "1 = en el cruce del Auto, los dos temas pasan de un tempo al otro solo en el tramo donde se oyen los dos (empieza suave cuando entra el nuevo y termina suave cuando se va el viejo),\n"
         "así el cambio queda tapado por la mezcla y no se nota en un tema solo. 0 = como antes (cambia de forma pareja con el crossfader, también cuando suena uno solo)."),
        ("AUTO_ENTRADA_ESTRUCTURA", "El tema que entra salta su intro larga (su 1.ª sección fuerte cae al final del cruce)", 0, 1, 1, "{:.0f}",
         "1 = el Auto mira la estructura del tema que entra (en frases de 8 compases): si tiene una intro larga, arranca más adentro para que su primera sección fuerte\n"
         "(cuando empieza la voz o sube la energía) caiga justo cuando termina el cruce, en vez de que la voz te pise a la mitad. Si el tema ya arranca fuerte, o la\n"
         "intro es más corta que el cruce, arranca de siempre. 0 = siempre desde su primer compás. Vale en los cruces que arranca el Auto (no cuando posicionás vos el tema)."),
        ("AUTO_OPTIMIZA", "Optimizar cada cruce del Auto (prueba variantes y elige la mejor)", 0, 1, 1, "{:.0f}",
         "1 = el Auto diseña varias versiones de cada cruce (distintas líneas de compás para el cambio de bajo; la duración no cambia), las puntúa\n"
         "(voces de los dos juntas, saltos de volumen, bajo de B que todavía no suena) y ejecuta la que menos molesta. Se queda cerca de tu plantilla.\n"
         "0 = hace exactamente lo que está en el panel de arriba. El motivo de cada elección queda en el registro («optimizado…»)."),
        ("PRO_COMPASES", "Compases de la mezcla Pro", 4, 32, 1, "{:.0f}",
         "Largo de la mezcla Pro en compases. Se usa el más cercano de 4, 8, 16 o 32 que quepa en lo que le queda al tema que sale.\n16 compases a 96 BPM son unos 40 s · 8 compases, unos 20 s."),
    ]),
    ("📉 Temas incompatibles (BPM muy distintos)", []),            # sin deslizadores: se arrastra el dibujo con el mouse
    ("⭐ Estadísticas de los temas", [
        ("EST_ESTRELLAS", "Mostrar las estrellas en la lista", 0, 1, 1, "{:.0f}",
         "1 = cada tema muestra de 1 a 5 estrellas amarillas según cuánto lo escuchaste comparado con los demás. 0 = ocultas (el conteo se sigue llevando por debajo\ny se ve en la ventana 📊 de estadísticas). El 👍 se ve siempre."),
        ("EST_PORC", "% del tema que hay que escuchar para contar una reproducción", 10, 95, 1, "{:.0f} %",
         "Cuánto tiene que sonar de verdad un tema para sumar una reproducción (y con eso, estrellas). Los saltos con el mouse no cuentan.\n50 % = la mitad del tema · 90 % = casi completo (ojo: en el Auto el cruce suele empezar antes del final, así que con valores muy altos pocos temas llegan)."),
    ]),
    ("🥁 Frase y compás", [
        ("ESPERA_FRASE", "Esperar a la frase (s máx., 0 = no)", 0, 30, 1, "{:.0f} s",
         "Al llegar a la marca roja del auto-enganche, la mezcla espera (como mucho este tiempo) al próximo inicio de frase (cada 8 compases)\ndel tema que sale, así arranca en el \"1\" de la frase. 0 = arranca justo en la marca. Solo si la grilla del tema es firme."),
    ]),
]


def cargar():
    """Al arrancar: lee lo guardado y lo aplica."""
    try:
        d = dict(leer_config().get(CLAVE, {}))
        if int(d.get("_ver", 1)) < 2:                      # la estrategia por defecto cambió (ahora 2 = como el manual): se ignora la guardada antes
            d.pop("ESTRATEGIA", None)
        cruce_auto.aplicar(d)
        if "AUTO_PA" not in d:                             # primera vez con la configuración propia del Auto: arranca como el manual que ya tenías
            cfg = leer_config()                            # solo se copia lo que el usuario ya había guardado en el manual; en una instalación nueva valen los valores por defecto del Auto
            viejo = {"AUTO_PA": "punto_a", "AUTO_PB": "punto_b", "AUTO_RB": "rampa_b", "AUTO_FA": "fin_a", "AUTO_G": "gamma_a", "AUTO_PS": "punto_s"}
            cruce_auto.aplicar({k: cfg[c] for k, c in viejo.items() if c in cfg})
            guardar()
    except Exception:
        import traceback; traceback.print_exc()


def guardar():
    try:
        guardar_config(**{CLAVE: dict(cruce_auto.valores(), _ver=2)})
    except Exception:
        pass


class _Tilde(QCheckBox):
    """Casilla para los ajustes de 0/1: se maneja igual que un deslizador (value / setValue / valueChanged) pero se ve como un tilde."""
    valueChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.toggled.connect(lambda on: self.valueChanged.emit(1 if on else 0))

    def setValue(self, v):
        self.setChecked(int(v) >= 1)

    def value(self):
        return 1 if self.isChecked() else 0


class _ValorVacio(QLabel):
    """Etiqueta de valor de las filas con tilde: no muestra nada (el tilde ya lo dice)."""
    def setText(self, _t):
        pass


class VistaRitmo(QWidget):
    """Dibujo del cruce (arriba de ⚙ Ajustes): cómo suben y bajan los dos temas en el tiempo, con la pelotita recorriéndolo, y se maneja con el mouse
    arrastrando los puntos:  ● celeste = duración · ● ámbar de arriba = pausa del 50 % · ● ámbar de abajo (2) = dónde empieza y dónde termina el frenado
    · ◆ blanco = cuánto frena en el cruce (arrastrar hacia arriba = más)."""
    BAR = 2.5                                          # s por compás del ejemplo (a 96 BPM)
    PAUSA_FINAL = 0.7                                  # s que se queda al final antes de reiniciar la pelotita

    def __init__(self, ventana, dlg=None):
        super().__init__()
        self.v = ventana
        self.d = dlg
        self.setFixedHeight(215)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setToolTip("Arrastrá los puntos:\n● celeste = cuánto dura el cruce\n● ámbar de arriba = pausa del 50 % (los dos temas suenan a la par)\n"
                        "● ámbar de abajo = dónde empieza a frenar el crossfader y dónde termina de acelerar\n◆ blanco = cuánto frena en el cruce (hacia arriba = más)\n"
                        "Solo con la estrategia 0 (clásica): mín / máx = duración mínima y máxima · rosa = cuándo empieza a entrar el tema nuevo (desde / hasta) y cuánto tarda en subir")
        self._t0 = time.perf_counter()
        self._reloj = QTimer(self); self._reloj.setTimerType(Qt.TimerType.PreciseTimer); self._reloj.setInterval(33)
        self._reloj.timeout.connect(self.update)
        self._arr = None; self._cand = []; self._x_ini = 0.0; self._y_ini = 0.0; self._v_ini = 1.5; self._eje_fijo = None

    def showEvent(self, ev):
        self._t0 = time.perf_counter(); self._reloj.start(); super().showEvent(ev)

    def hideEvent(self, ev):
        self._reloj.stop(); super().hideEvent(ev)

    # ---------- datos del cruce de ejemplo ----------
    def _caso(self):
        """(D, hay pausa, pc, pb de la zona, estrategia, título): el cruce que se dibuja con los ajustes de ahora."""
        e = _estrategia_activa()
        pc = pbz = None
        auto2 = cruce_auto.en_auto()                                  # ¿el dibujo del Auto «como mi manual» (su forma, su pausa) o el del cruce manual?
        if e != 1:                                                    # la Pro va pareja, sin frenado
            try:
                zona = cruce_auto.zona_auto() if auto2 else self.v._punto_cruce_manual()
            except Exception:
                zona = None
            if zona:
                pc, pbz = zona
        if e == 1:
            n, D = cruce_auto._n_pro(self.BAR, None)
            titulo = f"Pro · {n} compases ≈ {D:.0f} s"
        elif e == 2 and not auto2:                                    # el ritmo del cruce MANUAL: dura lo que marca «Duración» de la ventana principal
            try:
                D = float(self.v.spin_mix.value())
            except Exception:
                D = 15.0
            titulo = f"manual · {D:.0f} s"
        elif e == 2:
            if cruce_auto.MAN_COMPASES >= 1:
                n = int(round(cruce_auto.MAN_COMPASES)); D = min(cruce_auto.MAX_SEG_MANUAL, n * self.BAR)
                titulo = f"{n} compases ≈ {D:.0f} s en total"
            else:
                try:
                    D = min(cruce_auto.MAX_SEG_MANUAL, float(self.v.spin_mix.value()))
                except Exception:
                    D = 20.0
                titulo = f"{D:.0f} s en total"
            if cruce_auto.hay_pausa():                                # el tiempo estipulado INCLUYE la pausa: el crossfader recorre lo que queda
                D = max(2.0, D - cruce_auto.pausa_s())
        else:
            D = max(cruce_auto.MIN_DUR, min(cruce_auto.MAX_DUR, cruce_auto.DUR_IDEAL))
            titulo = f"clásico {D:.0f} s"
        pausa = (e != 1) and cruce_auto.hay_pausa()
        return float(D), bool(pausa), pc, pbz, e, titulo

    def _volumenes(self, pos):
        """(volumen del que sale, volumen del que entra) cuando el crossfader está en `pos` (0..1), con la forma de cruce de la ventana."""
        v = self.v
        if cruce_auto.en_auto():                                       # el Auto «como mi manual» tiene su propia forma
            fa = cruce_auto.AUTO_FA
            vs, ve, _l1, _l2 = perfil_cruce_bandas(pos, cruce_auto.AUTO_PA, cruce_auto.AUTO_PB, cruce_auto.AUTO_PS, rampa=cruce_auto.AUTO_RB,
                                                  fin_a=(None if fa >= 0.995 else fa), gamma_a=cruce_auto.AUTO_G)
            return vs, ve
        pa = getattr(v, "punto_a", DEF_PUNTO_A); pb = getattr(v, "punto_b", DEF_PUNTO_B)
        if not getattr(v, "cruce_ab", True):
            return math.cos(pos * math.pi / 2.0), math.sin(pos * math.pi / 2.0)
        if getattr(v, "cruce_bandas", False):
            fa = getattr(v, "fin_a", DEF_FIN_A)
            vs, ve, _l1, _l2 = perfil_cruce_bandas(pos, pa, pb, getattr(v, "punto_s", DEF_PUNTO_S), rampa=getattr(v, "rampa_b", DEF_RAMPA_B),
                                                  fin_a=(None if fa >= 0.995 else fa), gamma_a=getattr(v, "gamma_a", DEF_GAMMA_A))
            return vs, ve
        return perfil_cruce_ab(pos, pa, pb)

    @staticmethod
    def _tiempo_de(pos, D, pausa, pc, pbz):
        """Segundos en que el crossfader llega a esa posición (primera mitad)."""
        lo, hi = 0.0, D + (cruce_auto.pausa_s() if pausa else 0.0)                 # (hasta el final: la estrategia 2 recorre todo el crossfader con su ritmo)
        for _ in range(30):
            m = 0.5 * (lo + hi)
            if cruce_auto._t_de_T(m, D, pausa, pc, pbz) < pos:
                lo = m
            else:
                hi = m
        return hi

    # ---------- geometría ----------
    def _caja(self):
        return 40.0, self.width() - 16.0, 36.0, self.height() - 30.0       # x0, x1, yt, yb

    def _eje(self, total, e=None):
        if self._eje_fijo is not None:
            return self._eje_fijo
        if e == 0:                                                     # clásico: en el eje tiene que entrar también la duración máxima
            total = max(total, cruce_auto.MAX_DUR + (cruce_auto.pausa_s() if cruce_auto.hay_pausa() else 0.0))
        return max(30.0, math.ceil(total * 1.3 / 10.0) * 10.0)

    def _X(self, seg, eje):
        x0, x1, yt, yb = self._caja()
        return x0 + (x1 - x0) * max(0.0, min(1.0, seg / eje))

    def _seg(self, x, eje):
        x0, x1, yt, yb = self._caja()
        return max(0.0, min(eje, (x - x0) / max(1.0, x1 - x0) * eje))

    def _puntos(self):
        D, pausa, pc, pbz, e, _t = self._caso()
        pa = cruce_auto.pausa_s() if pausa else 0.0
        total = D + pa; eje = self._eje(total, e)
        x0, x1, yt, yb = self._caja()
        pts = {"dur": QPointF(self._X(total, eje), yb)}
        if e == 0:                                                     # solo en el clásico: duración mín/máx, cuándo entra el tema nuevo y cuánto tarda en subir
            y25 = yb - 0.25 * (yb - yt)
            pts["min_dur"] = QPointF(self._X(cruce_auto.MIN_DUR + pa, eje), yb); pts["max_dur"] = QPointF(self._X(cruce_auto.MAX_DUR + pa, eje), yb)
            pts["ent_min"] = QPointF(self._X(cruce_auto.ENT_MIN * D, eje), y25); pts["ent_max"] = QPointF(self._X(cruce_auto.ENT_MAX * D, eje), y25)
            pts["rb"] = QPointF(self._X((cruce_auto.ENT_MIN + cruce_auto.RB_MIN) * D, eje), yt)
        if e != 1:
            pts["pausa"] = QPointF(self._X(cruce_auto.frac_pausa(pc, pbz) * D + pa, eje), yt)
        if pc is not None:
            wa = max(cruce_auto.ZONA_MIN, pc - pbz) if pbz is not None else 0.10
            wd = max(wa, (0.5 - pc) * cruce_auto.ZONA_ACEL)
            Ti = self._tiempo_de(max(0.0, pc - wa), D, pausa, pc, pbz); Tf = self._tiempo_de(min(0.5, pc + wd), D, pausa, pc, pbz); Tc = self._tiempo_de(pc, D, pausa, pc, pbz)
            pts["frena_ini"] = QPointF(self._X(Ti, eje), yb); pts["frena_fin"] = QPointF(self._X(Tf, eje), yb)
            vs, _ve = self._volumenes(pc)
            pts["frenado"] = QPointF(self._X(Tc, eje), yb - vs * (yb - yt))
        return pts

    # ---------- dibujo ----------
    def paintEvent(self, ev):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        D, pausa, pc, pbz, e, titulo = self._caso()
        pa = cruce_auto.pausa_s() if pausa else 0.0
        total = D + pa; eje = self._eje(total, e)
        x0, x1, yt, yb = self._caja()
        X = lambda seg: self._X(seg, eje)
        ca, cb, am = QColor(COLOR_DECK_A), QColor(COLOR_DECK_B), QColor(TEMA["ambar"])
        p.setPen(QPen(QColor(TEMA["linea"]), 1)); p.setBrush(QColor("#070a12")); p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 8, 8)
        paso = 10 if eje <= 60 else 20
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        for seg in range(0, int(eje) + 1, paso):
            p.drawLine(QPointF(X(seg), yt), QPointF(X(seg), yb))
        p.drawLine(QPointF(x0, (yt + yb) / 2), QPointF(x1, (yt + yb) / 2))
        fs = QFont(self.font()); fs.setPointSizeF(8.0); p.setFont(fs)
        p.setPen(QColor("#8fa3b8"))
        p.drawText(QRectF(2, yt - 6, x0 - 6, 12), int(Qt.AlignmentFlag.AlignRight), "100 %"); p.drawText(QRectF(2, yb - 6, x0 - 6, 12), int(Qt.AlignmentFlag.AlignRight), "0")
        for seg in range(0, int(eje) + 1, paso):
            p.drawText(QRectF(X(seg) - 20, yb + 5, 40, 14), int(Qt.AlignmentFlag.AlignHCenter), f"{seg} s")
        pts = self._puntos()
        if pausa and pa > 0:                                                                   # banda de la pausa
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(am.red(), am.green(), am.blue(), 40))
            tq = cruce_auto.frac_pausa(pc, pbz) * D
            p.drawRect(QRectF(X(tq), yt, X(tq + pa) - X(tq), yb - yt))
        if "frena_ini" in pts:                                                                 # banda del frenado
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 255, 255, 16))
            p.drawRect(QRectF(pts["frena_ini"].x(), yt, pts["frena_fin"].x() - pts["frena_ini"].x(), yb - yt))
        n = 220
        pa_pts, pb_pts = [], []
        for i in range(n + 1):
            T = total * i / n
            vs, ve = self._volumenes(cruce_auto._t_de_T(T, D, pausa, pc, pbz))
            pa_pts.append(QPointF(X(T), yb - vs * (yb - yt))); pb_pts.append(QPointF(X(T), yb - ve * (yb - yt)))
        def curva(lista, c):
            g = QPolygonF(lista + [QPointF(lista[-1].x(), yb), QPointF(lista[0].x(), yb)])
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(c.red(), c.green(), c.blue(), 34)); p.drawPolygon(g)
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(c, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)); p.drawPolyline(QPolygonF(lista))
        curva(pa_pts, ca); curva(pb_pts, cb)
        # la pelotita: recorre el cruce en tiempo real
        ciclo = total + self.PAUSA_FINAL
        T = min((time.perf_counter() - self._t0) % max(0.1, ciclo), total)
        pos = cruce_auto._t_de_T(T, D, pausa, pc, pbz)
        vs, ve = self._volumenes(pos)
        bx = X(T)
        p.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt.PenStyle.DotLine)); p.drawLine(QPointF(bx, yt), QPointF(bx, yb))
        for val, c in ((vs, ca), (ve, cb)):
            by = yb - val * (yb - yt)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(c.red(), c.green(), c.blue(), 90)); p.drawEllipse(QPointF(bx, by), 8.5, 8.5)
            p.setBrush(QColor("#ffffff")); p.drawEllipse(QPointF(bx, by), 3.8, 3.8)
        # los puntos que se arrastran
        def etq(txt, pt, dx, dy, col):
            p.setPen(col); p.drawText(QRectF(pt.x() + dx, pt.y() + dy, 110, 12), int(Qt.AlignmentFlag.AlignLeft), txt)
        pt = pts["dur"]; p.setBrush(QColor("#ffffff")); p.setPen(QPen(ca, 2.4)); p.drawEllipse(pt, 6.5, 6.5)
        p.setPen(ca); p.drawText(QRectF(pt.x() - 58, pt.y() - 22, 80, 12), int(Qt.AlignmentFlag.AlignRight), "duración")
        if "pausa" in pts:
            pt = pts["pausa"]; p.setBrush(QColor("#ffffff")); p.setPen(QPen(am, 2.4)); p.drawEllipse(pt, 6.0, 6.0)
            etq(f"pausa {cruce_auto.pausa_s():.1f} s" if pausa else "sin pausa", pt, 9, -6, am)
        if "frena_ini" in pts:
            for k, txt in (("frena_ini", "frena"), ("frena_fin", "acelera")):
                pt = pts[k]; p.setBrush(QColor("#ffffff")); p.setPen(QPen(am, 2.2)); p.drawEllipse(pt, 5.0, 5.0)
                p.setPen(am); p.drawText(QRectF(pt.x() - 40, pt.y() - 20, 80, 12), int(Qt.AlignmentFlag.AlignHCenter), txt)
            pt = pts["frenado"]; r = 7.0
            p.setBrush(QColor("#ffffff")); p.setPen(QPen(QColor(TEMA["ambar"]), 2.0))
            p.drawPolygon(QPolygonF([QPointF(pt.x(), pt.y() - r), QPointF(pt.x() + r, pt.y()), QPointF(pt.x(), pt.y() + r), QPointF(pt.x() - r, pt.y())]))
            etq(f"frenado ×{cruce_auto.ZONA_V:.1f}", pt, 10, -6, QColor("#ffffff"))
        if "min_dur" in pts:
            for k, txt in (("min_dur", "mín"), ("max_dur", "máx")):
                pt = pts[k]; p.setPen(QPen(QColor(am.red(), am.green(), am.blue(), 120), 1, Qt.PenStyle.DashLine)); p.drawLine(QPointF(pt.x(), yt), QPointF(pt.x(), yb))
                p.setBrush(QColor("#ffffff")); p.setPen(QPen(am, 2.2)); p.drawEllipse(pt, 5.0, 5.0)
                p.setPen(am); p.drawText(QRectF(pt.x() - 40, pt.y() - 20, 80, 12), int(Qt.AlignmentFlag.AlignHCenter), txt)
            for k in ("ent_min", "ent_max"):
                pt = pts[k]; p.setPen(QPen(QColor(cb.red(), cb.green(), cb.blue(), 140), 1, Qt.PenStyle.DashLine)); p.drawLine(QPointF(pt.x(), pt.y()), QPointF(pt.x(), yb))
                p.setBrush(QColor("#ffffff")); p.setPen(QPen(cb, 2.2)); p.drawEllipse(pt, 5.0, 5.0)
            p.setPen(cb); p.drawText(QRectF(pts["ent_max"].x() + 9, pts["ent_max"].y() - 6, 200, 12), int(Qt.AlignmentFlag.AlignLeft),
                                     f"entra entre el {cruce_auto.ENT_MIN:.0%} y el {cruce_auto.ENT_MAX:.0%}")
            pt = pts["rb"]; p.setBrush(QColor("#ffffff")); p.setPen(QPen(cb, 2.2)); p.drawEllipse(pt, 5.0, 5.0)
            p.setPen(cb); p.drawText(QRectF(pt.x() + 8, pt.y() - 6, 120, 12), int(Qt.AlignmentFlag.AlignLeft), f"sube en {cruce_auto.RB_MIN:.0%}")
        p.setPen(ca); p.drawText(QRectF(x0, 6, 120, 14), int(Qt.AlignmentFlag.AlignLeft), "● Tema que suena")
        p.setPen(cb); p.drawText(QRectF(x0 + 112, 6, 110, 14), int(Qt.AlignmentFlag.AlignLeft), "● Tema nuevo")
        txt_t = f"{titulo}" + (f" + pausa {pa:.1f} s" if pausa else "")
        p.setPen(am); p.drawText(QRectF(x0 + 215, 6, x1 - x0 - 215, 14), int(Qt.AlignmentFlag.AlignRight),
                                 p.fontMetrics().elidedText(txt_t, Qt.TextElideMode.ElideLeft, int(x1 - x0 - 215)))
        p.setPen(QColor("#ffffff")); p.drawText(QRectF(x0 + 200, 20, x1 - x0 - 200, 12), int(Qt.AlignmentFlag.AlignRight), f"{T:.1f} s · crossfader {pos * 100:.0f} %")
        p.end()

    # ---------- mouse ----------
    def _cerca(self, pos):
        """Los puntos bajo el mouse (el más cercano y los pegados a él, por si se superponen), de izquierda a derecha."""
        pts = self._puntos()
        d = {k: (pos - pt).manhattanLength() for k, pt in pts.items()}
        if not d or min(d.values()) > 16.0:
            return []
        m = min(d.values())
        orden = ["frena_ini", "frena_fin", "min_dur", "dur", "max_dur", "pausa", "frenado", "ent_min", "ent_max", "rb"]
        return sorted((k for k in pts if d[k] <= m + 4.0), key=lambda k: (round(pts[k].x(), 1), orden.index(k)))

    def mousePressEvent(self, ev):
        c = self._cerca(ev.position())
        self._arr = c[0] if len(c) == 1 else None
        self._cand = c if len(c) > 1 else []
        self._x_ini = ev.position().x(); self._y_ini = ev.position().y(); self._v_ini = float(cruce_auto.ZONA_V)
        if c:
            D, pausa, _pc, _pb, e, _t = self._caso()
            self._eje_fijo = self._eje(D + (cruce_auto.pausa_s() if pausa else 0.0), e)      # mientras se arrastra el eje no cambia (si no, el punto se escapa)
        if self._arr:
            self._mover(ev.position())

    def mouseMoveEvent(self, ev):
        if not self._arr and self._cand:
            if abs(ev.position().x() - self._x_ini) >= 2.0 or abs(ev.position().y() - self._y_ini) >= 2.0:
                self._arr = self._cand[0] if ev.position().x() < self._x_ini else self._cand[-1]
                self._cand = []
        if self._arr:
            self._mover(ev.position())
        else:
            c = self._cerca(ev.position())
            self.setCursor(Qt.CursorShape.ArrowCursor if not c else (Qt.CursorShape.SizeVerCursor if c == ["frenado"] else Qt.CursorShape.SizeHorCursor))

    def mouseReleaseEvent(self, ev):
        self._arr = None; self._cand = []; self._eje_fijo = None

    def _aplicar(self, valores):
        if self.d is not None:
            self.d.auto_set(valores)
        else:
            cruce_auto.aplicar(valores); guardar(); self.update()

    def _mover(self, pos):
        k = self._arr
        D, pausa, pc, pbz, e, _t = self._caso()
        pa = cruce_auto.pausa_s() if pausa else 0.0
        eje = self._eje(D + pa, e)
        t = self._seg(pos.x(), eje)
        if k == "dur":
            Dn = max(2.0, t - pa)
            if e == 2 and cruce_auto.en_auto():
                Dn = max(2.0, t)                                             # (en «como mi manual» el tiempo ya incluye la pausa)
            if e == 0:
                ideal = float(max(6, min(40, round(Dn))))
                self._aplicar({"DUR_IDEAL": ideal, "MIN_DUR": min(cruce_auto.MIN_DUR, ideal), "MAX_DUR": max(cruce_auto.MAX_DUR, ideal)})
            elif e == 1:
                self._aplicar({"PRO_COMPASES": float(min((4, 8, 16, 32), key=lambda c: abs(c - Dn / self.BAR)))})
            elif cruce_auto.MAN_COMPASES >= 1 and cruce_auto.en_auto():
                self._aplicar({"MAN_COMPASES": float(max(2, min(12, round(Dn / self.BAR))))})
            else:
                try:
                    self.v.spin_mix.setValue(int(round(Dn)))                  # sin compases: es el deslizador «Duración» de la ventana principal
                except Exception:
                    pass
                self.update()
        elif k in ("min_dur", "max_dur"):
            val = float(round(t - pa))
            mn = max(4.0, min(40.0, val)) if k == "min_dur" else max(8.0, min(60.0, val))
            mi, ide, ma = cruce_auto.MIN_DUR, cruce_auto.DUR_IDEAL, cruce_auto.MAX_DUR
            if k == "min_dur":                                          # si pasa de la preferida o de la máxima, las empuja
                mi = mn; ide = max(ide, mi); ma = max(ma, ide)
            else:
                ma = mn; ide = min(ide, ma); mi = min(mi, ide)
            self._aplicar({"MIN_DUR": mi, "DUR_IDEAL": ide, "MAX_DUR": ma})
        elif k in ("ent_min", "ent_max"):
            f = t / max(1.0, D)
            em, ex = cruce_auto.ENT_MIN, cruce_auto.ENT_MAX
            if k == "ent_min":
                em = round(max(0.01, min(0.30, f)), 2); ex = max(ex, em)
            else:
                ex = round(max(0.02, min(0.40, f)), 2); em = min(em, ex)
            self._aplicar({"ENT_MIN": em, "ENT_MAX": ex})
        elif k == "rb":
            self._aplicar({"RB_MIN": round(max(0.02, min(0.30, t / max(1.0, D) - cruce_auto.ENT_MIN)), 2)})
        elif k == "pausa":
            self._aplicar({"PAUSA": round(max(0.0, min(10.0, t - cruce_auto.frac_pausa(pc, pbz) * D)) * 10.0) / 10.0})
        elif k in ("frena_ini", "frena_fin") and pc is not None:
            ps = cruce_auto._t_de_T(min(t, 0.5 * D), D, False, pc, pbz)             # posición del crossfader a ese segundo
            if k == "frena_ini":
                self._aplicar({"ZONA_MIN": round(max(0.01, min(0.25, pc - ps)), 2)})
            else:
                self._aplicar({"ZONA_ACEL": round(max(0.10, min(1.0, (ps - pc) / max(0.01, 0.5 - pc))), 2)})
        elif k == "frenado":
            x0, x1, yt, yb = self._caja()
            V = max(1.0, min(8.0, self._v_ini + (self._y_ini - pos.y()) / 120.0 * 7.0))
            self._aplicar({"ZONA_V": round(V, 1), "ZONA_K": 10.0})


class VistaIncompatible(QWidget):
    """Dibujo del fundido para temas incompatibles, que se maneja con el mouse arrastrando los puntos:
    ● celeste (abajo a la derecha): cuánto dura la bajada del tema que suena · ● rosa de abajo: cuándo entra el nuevo · ● rosa de arriba: cuándo llega a volumen pleno."""
    TMAX = 30.0                                                                   # el eje llega hasta 30 s (la bajada puede durar entre 2 y 30 s)

    def __init__(self, dlg):
        super().__init__()
        self.d = dlg
        self.setFixedHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setToolTip("Arrastrá los puntos:\n● celeste = cuánto dura la bajada del tema que suena\n● rosa de abajo = cuándo entra el tema nuevo\n● rosa de arriba = cuándo llega a volumen pleno")
        self._arr = None

    def _caja(self):
        return 40.0, self.width() - 16.0, 30.0, self.height() - 26.0          # x0, x1, yt, yb (arriba los títulos, abajo los segundos)

    def _X(self, seg):
        x0, x1, yt, yb = self._caja()
        return x0 + (x1 - x0) * max(0.0, min(1.0, seg / self.TMAX))

    def _seg(self, x):
        x0, x1, yt, yb = self._caja()
        return max(0.0, min(self.TMAX, (x - x0) / max(1.0, x1 - x0) * self.TMAX))

    def _datos(self):
        D = max(2.0, float(cruce_auto.INC_DUR)); te = D * (1.0 - float(cruce_auto.INC_ENTRA)); sub = float(cruce_auto.INC_SUBE)
        return D, te, min(D, te + sub)

    def _puntos(self):
        x0, x1, yt, yb = self._caja(); D, te, tf = self._datos()
        return {"dur": QPointF(self._X(D), yb), "entra": QPointF(self._X(te), yb), "sube": QPointF(self._X(tf), yt)}

    def paintEvent(self, ev):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(TEMA["linea"]), 1)); p.setBrush(QColor("#070a12")); p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 8, 8)
        x0, x1, yt, yb = self._caja(); D, te, tf = self._datos()
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        for seg in range(0, int(self.TMAX) + 1, 10):                                    # cuadrícula cada 10 s
            p.drawLine(QPointF(self._X(seg), yt), QPointF(self._X(seg), yb))
        for f in (0.5,):
            p.drawLine(QPointF(x0, yt + (yb - yt) * f), QPointF(x1, yt + (yb - yt) * f))
        fs = QFont(self.font()); fs.setPointSizeF(8.0); p.setFont(fs)
        p.setPen(QColor("#8fa3b8"))
        p.drawText(QRectF(2, yt - 6, x0 - 6, 12), int(Qt.AlignmentFlag.AlignRight), "100 %"); p.drawText(QRectF(2, yb - 6, x0 - 6, 12), int(Qt.AlignmentFlag.AlignRight), "0")
        for seg in range(0, int(self.TMAX) + 1, 10):
            p.drawText(QRectF(self._X(seg) - 20, yb + 5, 40, 14), int(Qt.AlignmentFlag.AlignHCenter), f"{seg} s")
        ca, cb = QColor(COLOR_DECK_A), QColor(COLOR_DECK_B)
        def poli(pts, c):
            g = QPolygonF(pts + [QPointF(pts[-1].x(), yb), QPointF(pts[0].x(), yb)])
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(c.red(), c.green(), c.blue(), 38)); p.drawPolygon(g)
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(c, 2.6)); p.drawPolyline(QPolygonF(pts))
        pts = self._puntos()
        poli([QPointF(x0, yt), pts["dur"]], ca)
        poli([QPointF(x0, yb), pts["entra"], pts["sube"], QPointF(pts["dur"].x(), yt)], cb)
        p.setPen(QPen(QColor(TEMA["ambar"]), 1, Qt.PenStyle.DashLine)); p.drawLine(QPointF(pts["entra"].x(), yt), QPointF(pts["entra"].x(), yb))
        for k, c in (("dur", ca), ("entra", cb), ("sube", cb)):
            p.setBrush(QColor("#ffffff")); p.setPen(QPen(c, 2.4)); p.drawEllipse(pts[k], 6.0, 6.0)
        p.setPen(QColor(COLOR_DECK_A)); p.drawText(QRectF(x0, 6, 120, 14), int(Qt.AlignmentFlag.AlignLeft), "● Tema que suena")
        p.setPen(QColor(COLOR_DECK_B)); p.drawText(QRectF(x0 + 112, 6, 110, 14), int(Qt.AlignmentFlag.AlignLeft), "● Tema nuevo")
        p.setPen(QColor(TEMA["ambar"]))
        p.drawText(QRectF(x0 + 200, 6, x1 - x0 - 200, 14), int(Qt.AlignmentFlag.AlignRight),
                   f"Baja en {D:.0f} s · el nuevo entra a los {te:.1f} s (queda {float(cruce_auto.INC_ENTRA):.0%})")
        p.end()

    def _cerca(self, pos):
        pts = self._puntos()
        for k in ("sube", "entra", "dur"):
            if (pos - pts[k]).manhattanLength() <= 15:
                return k
        return None

    def mousePressEvent(self, ev):
        self._arr = self._cerca(ev.position())
        if self._arr:
            self._mover(ev.position())

    def mouseMoveEvent(self, ev):
        if self._arr:
            self._mover(ev.position())
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._cerca(ev.position()) else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, ev):
        self._arr = None

    def _mover(self, pos):
        D, te, tf = self._datos(); t = self._seg(pos.x())
        if self._arr == "dur":
            self.d.inc_set("INC_DUR", float(max(2, min(int(self.TMAX), round(t)))))
        elif self._arr == "entra":
            self.d.inc_set("INC_ENTRA", round(max(0.0, min(0.5, 1.0 - t / D)), 2))
        elif self._arr == "sube":
            self.d.inc_set("INC_SUBE", round(max(0.0, min(3.0, t - te)), 1))


class GraficoCruceSimple(VistaRitmo):
    """El dibujo del panel nuevo (estrategia «como mi manual»): las dos curvas, la pausa y tres marcas que se arrastran:
    ● naranja = dónde se detiene el cruce · ▮ naranja = cuánto dura la pausa · ● roja = hasta dónde acelera · ● amarilla = el fin (la duración)."""
    ROJO = "#ef4444"
    AMARILLO = "#fde047"

    def event(self, ev):
        """Todo lo que pasa en este dibujo (pintar, mouse) se hace con la pausa y el ritmo PROPIOS del Auto, no los del manual."""
        with cruce_auto.contexto(True):
            return super().event(ev)

    def __init__(self, ventana, dlg=None):
        super().__init__(ventana, dlg)
        self.setFixedHeight(200)
        self.setToolTip("Arrastrá las marcas:\n● naranja = dónde se detiene el cruce (los dos temas suenan a la par)\n▮ naranja (borde derecho de la banda) = cuánto dura la pausa\n"
                        "● roja = hasta dónde sigue frenado antes de acelerar\n● amarilla = el final del cruce (la duración)")

    @staticmethod
    def _r(pc):
        """Posición del crossfader donde termina la aceleración (la marca roja): la que se eligió, o el cruce de las dos curvas."""
        q = cruce_auto.pausa_pos()
        r = float(cruce_auto.ACEL_POS) if cruce_auto.ACEL_POS > 0 else (float(pc) if pc is not None else 0.3)
        return min(max(r, 0.05), q - 0.03)

    @staticmethod
    def _tq(D, pc, pbz):
        return cruce_auto.frac_pausa(pc, pbz) * D                       # segundo en que empieza la pausa

    def _y_pausa(self):
        """Altura del punto naranja: sobre la curva del tema que sale, en la posición donde se detiene."""
        x0, x1, yt, yb = self._caja()
        vs, _ve = self._volumenes(cruce_auto.pausa_pos())
        return yb - vs * (yb - yt)

    def _puntos(self):
        D, pausa, pc, pbz, e, _t = self._caso()
        pa = cruce_auto.pausa_s() if pausa else 0.0
        eje = self._eje(D + pa, e)
        x0, x1, yt, yb = self._caja()
        tq = self._tq(D, pc, pbz)
        yp = self._y_pausa()
        pts = {"dur": QPointF(self._X(D + pa, eje), yb), "pausa": QPointF(self._X(tq, eje), yp)}
        if pausa and pa > 0:
            pts["pausa_len"] = QPointF(self._X(tq + pa, eje), yp)
        pts["acel"] = QPointF(self._X(self._tiempo_de(self._r(pc), D, pausa, pc, pbz), eje), yb)
        return pts

    def _cerca(self, pos):
        pts = self._puntos()
        d = {k: (pos - pt).manhattanLength() for k, pt in pts.items()}
        if not d or min(d.values()) > 16.0:
            return []
        m = min(d.values())
        orden = ["acel", "pausa", "pausa_len", "dur"]
        return sorted((k for k in pts if d[k] <= m + 4.0), key=lambda k: (round(pts[k].x(), 1), orden.index(k)))

    def paintEvent(self, ev):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        D, pausa, pc, pbz, e, titulo = self._caso()
        pa = cruce_auto.pausa_s() if pausa else 0.0
        total = D + pa; eje = self._eje(total, e)
        x0, x1, yt, yb = self._caja()
        X = lambda seg: self._X(seg, eje)
        ca, cb, am = QColor(COLOR_DECK_A), QColor(COLOR_DECK_B), QColor(TEMA["ambar"])
        rojo, amar = QColor(self.ROJO), QColor(self.AMARILLO)
        p.setPen(QPen(QColor(TEMA["linea"]), 1)); p.setBrush(QColor("#070a12")); p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 8, 8)
        paso = 10 if eje <= 60 else 20
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        for seg in range(0, int(eje) + 1, paso):
            p.drawLine(QPointF(X(seg), yt), QPointF(X(seg), yb))
        p.drawLine(QPointF(x0, (yt + yb) / 2), QPointF(x1, (yt + yb) / 2))
        fs = QFont(self.font()); fs.setPointSizeF(8.0); p.setFont(fs)
        p.setPen(QColor("#8fa3b8"))
        p.drawText(QRectF(2, yt - 6, x0 - 6, 12), int(Qt.AlignmentFlag.AlignRight), "100 %"); p.drawText(QRectF(2, yb - 6, x0 - 6, 12), int(Qt.AlignmentFlag.AlignRight), "0")
        for seg in range(0, int(eje) + 1, paso):
            p.drawText(QRectF(X(seg) - 20, yb + 4, 40, 14), int(Qt.AlignmentFlag.AlignHCenter), f"{seg} s")
        pts = self._puntos()
        tq = self._tq(D, pc, pbz)
        if pausa and pa > 0:                                                                   # banda de la pausa
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(am.red(), am.green(), am.blue(), 46))
            p.drawRect(QRectF(X(tq), yt, X(tq + pa) - X(tq), yb - yt))
        n = 220
        pa_pts, pb_pts = [], []
        for i in range(n + 1):
            T = total * i / n
            vs, ve = self._volumenes(cruce_auto._t_de_T(T, D, pausa, pc, pbz))
            pa_pts.append(QPointF(X(T), yb - vs * (yb - yt))); pb_pts.append(QPointF(X(T), yb - ve * (yb - yt)))
        def curva(lista, c):
            g = QPolygonF(lista + [QPointF(lista[-1].x(), yb), QPointF(lista[0].x(), yb)])
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(c.red(), c.green(), c.blue(), 34)); p.drawPolygon(g)
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(c, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)); p.drawPolyline(QPolygonF(lista))
        curva(pa_pts, ca); curva(pb_pts, cb)
        # la pelotita: recorre el cruce en tiempo real
        ciclo = total + self.PAUSA_FINAL
        T = min((time.perf_counter() - self._t0) % max(0.1, ciclo), total)
        pos = cruce_auto._t_de_T(T, D, pausa, pc, pbz)
        vs, ve = self._volumenes(pos)
        bx = X(T)
        p.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt.PenStyle.DotLine)); p.drawLine(QPointF(bx, yt), QPointF(bx, yb))
        for val, c in ((vs, ca), (ve, cb)):
            by = yb - val * (yb - yt)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(c.red(), c.green(), c.blue(), 90)); p.drawEllipse(QPointF(bx, by), 8.5, 8.5)
            p.setBrush(QColor("#ffffff")); p.drawEllipse(QPointF(bx, by), 3.8, 3.8)
        # marca amarilla: el fin del cruce
        pt = pts["dur"]
        p.setPen(QPen(amar, 2.0)); p.drawLine(QPointF(pt.x(), yt), QPointF(pt.x(), yb))
        p.setBrush(QColor("#ffffff")); p.setPen(QPen(amar, 2.4)); p.drawEllipse(pt, 6.0, 6.0)
        p.setPen(amar); p.drawText(QRectF(pt.x() - 100, yb - 22, 92, 12), int(Qt.AlignmentFlag.AlignRight), "fin")
        # línea verde: en qué momento se traspasa el bajo («Bajo (cambia)»)
        ps = cruce_auto.AUTO_PS
        tb = ps * D + (pa if (pausa and ps * D >= tq) else 0.0)
        xb = X(tb); verde = QColor(TEMA["verde"])
        p.setPen(QPen(verde, 1.8, Qt.PenStyle.DashLine)); p.drawLine(QPointF(xb, yt + 14), QPointF(xb, yb))
        p.setPen(verde); p.drawText(QRectF(xb + 4, yt + 14, 60, 12), int(Qt.AlignmentFlag.AlignLeft), "bajo")
        # marca roja: hasta dónde acelera
        if "acel" in pts:
            pt = pts["acel"]
            p.setPen(QPen(rojo, 2.0)); p.drawLine(QPointF(pt.x(), yt), QPointF(pt.x(), yb))
            p.setBrush(QColor("#ffffff")); p.setPen(QPen(rojo, 2.4)); p.drawEllipse(pt, 6.0, 6.0)
            xa = pt.x(); xq = pts["pausa"].x()
            p.setPen(QPen(QColor(rojo.red(), rojo.green(), rojo.blue(), 150), 1.4)); p.drawLine(QPointF(x0 + 4, yb - 8), QPointF(xa - 4, yb - 8))
            p.drawLine(QPointF(xa - 10, yb - 12), QPointF(xa - 4, yb - 8)); p.drawLine(QPointF(xa - 10, yb - 4), QPointF(xa - 4, yb - 8))
            if xa - x0 > 70:
                p.setPen(rojo); p.drawText(QRectF(x0 + 4, yb - 24, xa - x0 - 8, 12), int(Qt.AlignmentFlag.AlignHCenter), "acelera")
            if xq - xa > 60:
                p.setPen(QColor("#c4b5fd")); p.drawText(QRectF(xa + 4, yb - 24, xq - xa - 8, 12), int(Qt.AlignmentFlag.AlignHCenter), f"frena ×{cruce_auto.S2_FRENO:.1f}")
        # punto naranja: dónde se detiene + el borde que estira la pausa
        pt = pts["pausa"]
        p.setPen(QPen(QColor(am.red(), am.green(), am.blue(), 140), 1.4, Qt.PenStyle.DashLine)); p.drawLine(QPointF(pt.x(), pt.y()), QPointF(pt.x(), yb))
        if "pausa_len" in pts:
            pl = pts["pausa_len"]
            p.setPen(QPen(am, 2.0)); p.drawLine(pt, pl)
            p.setBrush(QColor("#ffffff")); p.setPen(QPen(am, 2.2)); p.drawRoundedRect(QRectF(pl.x() - 4.5, pl.y() - 7.0, 9.0, 14.0), 2.5, 2.5)
        p.setBrush(QColor(am)); p.setPen(QPen(QColor("#ffffff"), 2.2)); p.drawEllipse(pt, 7.5, 7.5)
        etx = f"pausa {cruce_auto.pausa_s():.1f} s" if pausa else "sin pausa"
        p.setPen(am); p.drawText(QRectF(pt.x() + 11, pt.y() - 31, 150, 12), int(Qt.AlignmentFlag.AlignLeft), etx)
        p.setPen(QColor("#c9d6e6")); p.drawText(QRectF(pt.x() + 11, pt.y() - 18, 150, 12), int(Qt.AlignmentFlag.AlignLeft), f"se detiene al {cruce_auto.pausa_pos() * 100:.0f} %")
        # arriba: leyenda y datos
        p.setPen(ca); p.drawText(QRectF(x0, 6, 120, 14), int(Qt.AlignmentFlag.AlignLeft), "● Tema que suena")
        p.setPen(cb); p.drawText(QRectF(x0 + 112, 6, 110, 14), int(Qt.AlignmentFlag.AlignLeft), "● Tema nuevo")
        txt_t = f"{titulo}" + (f" (pausa {pa:.1f} s incluida)" if pausa else "")
        p.setPen(am); p.drawText(QRectF(x0 + 215, 6, x1 - x0 - 215, 14), int(Qt.AlignmentFlag.AlignRight),
                                 p.fontMetrics().elidedText(txt_t, Qt.TextElideMode.ElideLeft, int(x1 - x0 - 215)))
        p.setPen(QColor("#ffffff")); p.drawText(QRectF(x0 + 200, 20, x1 - x0 - 200, 12), int(Qt.AlignmentFlag.AlignRight), f"{T:.1f} s · crossfader {pos * 100:.0f} %")
        p.end()

    def _mover(self, pos):
        k = self._arr
        if k == "dur":
            return super()._mover(pos)                                  # la duración: igual que en el dibujo de siempre
        D, pausa, pc, pbz, e, _t = self._caso()
        pa = cruce_auto.pausa_s() if pausa else 0.0
        eje = self._eje(D + pa, e)
        t = self._seg(pos.x(), eje)
        if k == "pausa":                                                 # dónde se detiene: la posición del crossfader a ese segundo
            q = cruce_auto.pos_cruce(max(0.0, min(1.0, t / max(1.0, D))), pc, pbz)
            self._aplicar({"AUTO_PAUSA_POS": round(max(0.25, min(0.75, q)), 2)})
        elif k == "pausa_len":                                           # cuánto dura: de medio en medio segundo, hasta 10 s
            self._aplicar({"AUTO_PAUSA": round(max(0.0, min(10.0, t - self._tq(D, pc, pbz))) * 2.0) / 2.0})
        elif k == "acel":                                                # hasta dónde acelera; cerca del cruce de las curvas se pega ahí (= automático)
            ps = cruce_auto._t_de_T(t, D, pausa, pc, pbz)
            ps = max(0.05, min(cruce_auto.pausa_pos() - 0.03, ps))
            auto = pc is not None and abs(ps - pc) < 0.02
            self._aplicar({"ACEL_POS": 0.0 if auto else round(ps, 2)})


class PanelCruceAuto(QFrame):
    """Panel de ⚙ Ajustes para la estrategia «como mi manual»: la MISMA forma de cruce que el panel «Forma de cruce A/B» del frente
    (gráfico + A, B, B sube, A termina, Forma de A, Bajo, Pausa en, Pausa dura), pero con la configuración PROPIA del Auto:
    moverla no cambia los deslizadores del cruce manual (y al revés). Es la base: cada mezcla parte de acá y el Auto ajusta duración y línea de compás."""
    def __init__(self, ventana, dlg):
        super().__init__()
        from setup.panel_cruce import GraficoCruce
        self.v = ventana
        self.d = dlg
        self.setObjectName("panelnuevo")
        self.setStyleSheet(qss_marco("QFrame#panelnuevo", "#3b82f6", titulo=False))
        lay = QVBoxLayout(self); lay.setContentsMargins(6, 5, 6, 5); lay.setSpacing(2)
        tit = QLabel("🤖 Forma de cruce del AUTO (la base de cada mezcla automática)"); tit.setStyleSheet(f"color:{TEMA['cian']}; font-weight:bold;")
        lay.addWidget(tit)
        self.graf = GraficoCruce(); self.graf.setMinimumHeight(85)
        lay.addWidget(self.graf)
        gl = QGridLayout(); gl.setHorizontalSpacing(8); gl.setVerticalSpacing(1); gl.setColumnStretch(1, 1)
        lay.addLayout(gl)
        self._filas = {}                                                # clave -> (deslizador, valor, función que lo lee, formato)
        self._bloq = False
        # (clave, texto, color, mínimo, máximo, lee, formato, cómo se guarda, ayuda): las mismas filas y rangos que el panel del frente
        filas = (
            ("pa", "A (sale):", COLOR_DECK_A, 0, 95, lambda: cruce_auto.AUTO_PA * 100, lambda x: f"{x}%", lambda x: {"AUTO_PA": x / 100.0},
             "Momento en que el tema que sale comienza a bajar."),
            ("pb", "B (entra):", COLOR_DECK_B, 0, 95, lambda: cruce_auto.AUTO_PB * 100, lambda x: f"{x}%", lambda x: {"AUTO_PB": x / 100.0},
             "Momento en que el tema que entra comienza a subir."),
            ("rb", "B sube en:", COLOR_DECK_B, 2, 40, lambda: cruce_auto.AUTO_RB * 100, lambda x: f"{x}%", lambda x: {"AUTO_RB": x / 100.0},
             "Cuánto tarda B en subir a pleno una vez que entra. Poco = entra de golpe · mucho = sube despacio."),
            ("fa", "A termina en:", COLOR_DECK_A, 50, 100, lambda: cruce_auto.AUTO_FA * 100, lambda x: f"{x}%", lambda x: {"AUTO_FA": x / 100.0},
             "En qué punto termina de irse A. 100 % = baja hasta el final."),
            ("g", "Forma de A:", COLOR_DECK_A, 10, 25, lambda: cruce_auto.AUTO_G * 10, lambda x: f"{x / 10:.1f}", lambda x: {"AUTO_G": x / 10.0},
             "Forma de la caída de A. 1,0 = la curva de siempre · más alto = A achica sus medios y agudos antes."),
            ("ps", "Bajo (cambia):", TEMA["verde"], 0, 95, lambda: cruce_auto.AUTO_PS * 100, lambda x: f"{x}%", lambda x: {"AUTO_PS": x / 100.0},
             "Momento de la mezcla en que el bajo pasa del tema que sale al que entra (en la línea de compás más próxima)."),
            ("pp", "Pausa en:", TEMA["ambar"], 25, 75, lambda: cruce_auto.AUTO_PAUSA_POS * 100, lambda x: f"{x}%", lambda x: {"AUTO_PAUSA_POS": x / 100.0},
             "Punto del cruce donde se frena: cuando la línea blanca llega a la línea ámbar, el cruce se queda quieto el tiempo de «Pausa dura»."),
        )
        for r, (k, texto, color, lo, hi, leer, fmt, guardar_v, tip) in enumerate(filas):
            sl, lv = self._fila(gl, r, texto, color, lo, hi, tip + "\n(Es solo del Auto: no cambia el panel «Forma de cruce A/B» del manual.)")
            self._filas[k] = (sl, lv, leer, fmt)
            sl.valueChanged.connect(lambda x, g=guardar_v: self._aplicar_cruce(g(x)))
        r = len(filas)
        l_pd = QLabel("Pausa dura:"); l_pd.setStyleSheet(f"color:{TEMA['ambar']}; font-weight:bold;")
        self.cb_pd = QComboBox()
        for n in range(10):
            self.cb_pd.addItem(f"{n} s" if n else "Sin pausa", n)
        self.cb_pd.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tip_pd = "Cuántos segundos se queda el cruce en la línea ámbar (0 = no se frena)."
        l_pd.setToolTip(tip_pd); self.cb_pd.setToolTip(tip_pd)
        self.cb_pd.currentIndexChanged.connect(lambda _i: self._aplicar_cruce({"AUTO_PAUSA": float(self.cb_pd.currentData() or 0)}))
        gl.addWidget(l_pd, r, 0); gl.addWidget(self.cb_pd, r, 1, 1, 2)
        sl, lv = self._fila(gl, r + 1, "Tiempo total:", "#ffffff", 5, 30, "Cuánto dura el cruce del Auto en total, con la pausa incluida.")
        lv.setMinimumWidth(92)
        self._tiempo = (sl, lv)
        sl.valueChanged.connect(self._cambio_tiempo)
        self.sincronizar()

    @staticmethod
    def _fila(gl, r, texto, color, lo, hi, tip):
        l = QLabel(texto); l.setStyleSheet(f"color:{color}; font-weight:bold;"); l.setToolTip(tip)
        sl = QSlider(Qt.Orientation.Horizontal); sl.setRange(int(lo), int(hi)); sl.setToolTip(tip)
        lv = QLabel(); lv.setMinimumWidth(46); lv.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        gl.addWidget(l, r, 0); gl.addWidget(sl, r, 1); gl.addWidget(lv, r, 2)
        return sl, lv

    def _aplicar_cruce(self, valores):
        if self._bloq:
            return
        self.d.auto_set(valores)

    def _cambio_tiempo(self, x):
        if self._bloq:
            return
        self.d.auto_set({"AUTO_SEG": float(x)})
        self.sincronizar()

    def _dibujar(self):
        """Repinta el gráfico con la forma PROPIA del Auto."""
        fa = cruce_auto.AUTO_FA
        pd = int(round(cruce_auto.AUTO_PAUSA))
        self.graf.actualizar(cruce_auto.AUTO_PA, cruce_auto.AUTO_PB, None, cruce_auto.AUTO_PS, True, None if fa >= 0.995 else fa,
                             rb=cruce_auto.AUTO_RB, gamma=cruce_auto.AUTO_G,
                             pausa=(cruce_auto.AUTO_PAUSA_POS, float(pd)) if pd > 0 else None)

    def sincronizar(self):
        """Deja todos los deslizadores con los valores de ahora (los cambió el panel, los valores por defecto o la estrategia)."""
        self._bloq = True
        try:
            for k, (sl, lv, leer, fmt) in self._filas.items():
                sl.setValue(int(round(leer()))); lv.setText(fmt(sl.value()))
            self.cb_pd.setCurrentIndex(max(0, min(9, int(round(cruce_auto.AUTO_PAUSA)))))
            sl, lv = self._tiempo
            sl.setRange(5, 30); sl.setValue(max(5, min(30, int(round(cruce_auto.AUTO_SEG)))))
            lv.setText(f"{sl.value()} s")
            sl.setToolTip("Cuánto dura el cruce del Auto en total, con la pausa incluida. Dura exactamente esto (solo se acorta si al tema que sale no le queda tanto).")
        finally:
            self._bloq = False
        self._dibujar()


class AjustesCruce(QDialog):
    def __init__(self, ventana):
        super().__init__(ventana)
        self.v = ventana
        self.setWindowTitle("⚙ Ajustes generales — tiempos y retardo del cruce")
        self.setMinimumWidth(580)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)
        info = QLabel("Acá se regulan los tiempos del cruce: arrastrá los puntos del dibujo con el mouse o usá el «Ajuste fino» de al lado (pausa, frenado). La línea celeste es el tema que suena "
                      "y la rosa el que entra; la pelotita recorre el cruce. Los cambios se guardan solos y valen desde la próxima mezcla.")
        info.setWordWrap(True); info.setStyleSheet("color:#9fe3c6; font-size:11px;")
        self._info = info
        self._texto_info_viejo = info.text()
        self.vista = VistaRitmo(ventana, self)
        self._cont_viejo = QWidget()                    # el dibujo de siempre + «Ajuste fino» (estrategias 0 y 1)
        fila_v = QHBoxLayout(self._cont_viejo); fila_v.setContentsMargins(0, 0, 0, 0); fila_v.setSpacing(8)
        fila_v.addWidget(self.vista, 1); fila_v.addWidget(self._crear_panel_fino(), 0)
        self.panel_nuevo = PanelCruceAuto(ventana, self)  # el panel nuevo (estrategia 2 «como mi manual»): configuración PROPIA del Auto
        self.vista_nueva = self.panel_nuevo.graf
        # ---- solapas: cada una con su propia zona con scroll (el formulario no pasa del alto de la pantalla) ----
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(False)
        self._tabs.setStyleSheet("QTabWidget::pane { border:1px solid #2a3a5c; border-radius:8px; top:-1px; }"
                                 "QTabBar::tab { background:#131a2c; color:#cfe3ff; padding:6px 12px; border:1px solid #2a3a5c; border-bottom:none;"
                                 " border-top-left-radius:8px; border-top-right-radius:8px; margin-right:2px; }"
                                 "QTabBar::tab:selected { background:#1f3b63; color:#ffffff; border-color:#4fd1ff; font-weight:bold; }"
                                 "QTabBar::tab:hover:!selected { background:#1b2b4a; }")

        def _pagina(titulo_tab):
            cont_p = QWidget(); cl_p = QVBoxLayout(cont_p); cl_p.setContentsMargins(4, 6, 8, 4); cl_p.setSpacing(4)
            sc_p = QScrollArea(); sc_p.setWidgetResizable(True); sc_p.setFrameShape(QFrame.Shape.NoFrame); sc_p.setWidget(cont_p)
            sc_p.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._tabs.addTab(sc_p, titulo_tab)
            return cl_p
        pag_forma = _pagina("🤖 Forma del cruce")
        pag_forma.addWidget(info); pag_forma.addWidget(self.panel_nuevo); pag_forma.addWidget(self._cont_viejo); pag_forma.addStretch(1)
        paginas = {"estrategia": _pagina("🎛 Estrategia"), "incompatibles": _pagina("📉 Incompatibles"), "estadisticas": _pagina("⭐ Estadísticas")}
        tab_de = {"🎛": "estrategia", "🥁": "estrategia", "📉": "incompatibles", "⭐": "estadisticas"}      # qué grupo va en qué solapa
        self._sl = {}
        self._filas_w = {}                              # clave -> (etiqueta, deslizador, valor)
        self._cajas = []                                # (caja, [claves])
        for titulo, filas in GRUPOS:
            caja = QGroupBox(titulo)
            gl = QGridLayout(caja)
            gl.setContentsMargins(8, 12, 8, 3); gl.setHorizontalSpacing(8); gl.setVerticalSpacing(1)
            r0 = 0
            claves_caja = [f[0] for f in filas]
            if titulo.startswith("📉"):                     # este grupo es solo el dibujo (se arrastra con el mouse) y una fila con el interruptor y el límite de BPM
                self.vista_inc = VistaIncompatible(self)
                gl.addWidget(self.vista_inc, 0, 0, 1, 3)
                fila_i = QHBoxLayout(); fila_i.setContentsMargins(0, 0, 0, 0)
                self.chk_inc = QCheckBox("Activado"); self.chk_inc.setChecked(cruce_auto.INC_ACTIVO >= 0.5)
                self.chk_inc.setToolTip("Activado: con temas incompatibles el que suena baja en rampa y el nuevo entra a su tempo natural.\nDesactivado: el cruce simple de siempre.")
                self.chk_inc.toggled.connect(lambda on: self.inc_set("INC_ACTIVO", 1.0 if on else 0.0))
                self.sp_inc = QSpinBox(); self.sp_inc.setRange(5, 60); self.sp_inc.setSuffix(" %"); self.sp_inc.setValue(int(round(cruce_auto.INC_BPM * 100)))
                self.sp_inc.setToolTip("Diferencia de BPM entre los dos temas a partir de la cual se consideran incompatibles (no se emparejan).")
                self.sp_inc.valueChanged.connect(lambda v: self.inc_set("INC_BPM", v / 100.0))
                fila_i.addWidget(self.chk_inc); fila_i.addStretch(1); fila_i.addWidget(QLabel("Incompatibles si el BPM difiere más de")); fila_i.addWidget(self.sp_inc)
                gl.addLayout(fila_i, 1, 0, 1, 3)
                r0 = 2
            for r, (k, texto, lo, hi, div, fmt, tip) in enumerate(filas, r0):
                lbl = QLabel(texto); lbl.setToolTip(tip)
                lbl.setWordWrap(True); lbl.setMinimumWidth(60)         # las etiquetas largas se parten en dos líneas en vez de ensanchar la ventana
                if (lo, hi) == (0, 1):                          # sí / no: un tilde, no un deslizador
                    sl = _Tilde(); sl.setToolTip(tip)
                    val = _ValorVacio(); val.setMinimumWidth(56)
                else:
                    sl = QSlider(Qt.Orientation.Horizontal); sl.setRange(lo, hi); sl.setToolTip(tip); sl.setMinimumWidth(110)
                    val = QLabel(); val.setMinimumWidth(56); val.setAlignment(Qt.AlignmentFlag.AlignRight)
                sl.setValue(int(round(getattr(cruce_auto, k) * div)))
                sl.valueChanged.connect(lambda x, k=k, div=div, fmt=fmt, val=val: self._cambio(k, x, div, fmt, val))
                val.setText(fmt.format(sl.value() / div))
                gl.addWidget(lbl, r, 0); gl.addWidget(sl, r, 1); gl.addWidget(val, r, 2)
                gl.setColumnStretch(0, 5); gl.setColumnStretch(1, 1)
                self._sl[k] = (sl, val, div, fmt)
                self._filas_w[k] = (lbl, sl, val)
            if titulo.startswith("⭐"):                     # botón para dejar el conteo en cero (las estrellas vuelven a empezar)
                self.b_est_borrar = QPushButton("🗑 Borrar todas las estrellas (poner el conteo en cero)")
                self.b_est_borrar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                self.b_est_borrar.setToolTip("Pone en cero las reproducciones de todos los temas, así las estrellas empiezan de nuevo. Los 👍 se mantienen. No se puede deshacer.")
                self.b_est_borrar.clicked.connect(self._borrar_estrellas)
                gl.addWidget(self.b_est_borrar, len(filas), 0, 1, 3)
            self._cajas.append((caja, claves_caja))
            paginas[tab_de.get(titulo[:1], "estrategia")].addWidget(caja)
        for cl_p in paginas.values():
            cl_p.addStretch(1)
        lay.addWidget(self._tabs, 1)
        fila = QHBoxLayout()
        b_def = QPushButton("Valores por defecto"); b_def.clicked.connect(self._defecto)
        self.b_olv = QPushButton("🧠 Olvidar lo aprendido")
        self.b_olv.setToolTip("Borra los 👍 / 👎 guardados: el Auto vuelve a hacer exactamente lo que dice el panel.")
        self.b_olv.clicked.connect(self._olvidar)
        self.b_datos = QPushButton("📂 Abrir carpeta de datos")
        self.b_datos.setToolTip(f"Abre la carpeta donde AutoMix guarda todo lo analizado, tu lista, los 👍 y las estadísticas:\n{CACHE_DIR}\n"
                                "Cópiala para tener un respaldo; si la pones en otro equipo (o la recuperas después de reinstalar) no hay que volver a analizar.")
        self.b_datos.clicked.connect(self._abrir_datos)
        b_cer = QPushButton("Cerrar"); b_cer.clicked.connect(self.close)
        for b in (b_def, self.b_olv, self.b_datos, b_cer):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        fila.addWidget(b_def); fila.addWidget(self.b_olv); fila.addWidget(self.b_datos); fila.addStretch(1); fila.addWidget(b_cer)
        lay.addLayout(fila)
        self._visibles()
        try:
            alto = int(self.screen().availableGeometry().height() * 0.60)
        except Exception:
            alto = 800
        self.resize(640, min(alto, 600))

    def inc_set(self, k, v):
        """Cambia un valor del fundido de temas incompatibles (desde el dibujo, el interruptor o el límite de BPM) y lo guarda."""
        cruce_auto.aplicar({k: v})
        guardar()
        if hasattr(self, "vista_inc"):
            self.vista_inc.update()

    def _crear_panel_fino(self):
        """Deslizadores al costado del dibujo (como los de «Forma de cruce»): para ajustar con precisión lo que también se mueve con los puntos."""
        marco = QFrame(); marco.setObjectName("panelfino")
        marco.setStyleSheet(qss_marco("QFrame#panelfino", "#3b82f6", titulo=False))
        marco.setFixedWidth(236)
        gl = QGridLayout(marco); gl.setContentsMargins(8, 8, 8, 8); gl.setVerticalSpacing(7); gl.setHorizontalSpacing(6)
        am = TEMA["ambar"]
        # (clave, texto, color, mínimo, máximo, valor desde cruce_auto, formato, cómo se guarda, ayuda)
        filas = (
            ("pausa", "Pausa:", am, 0, 100, lambda: cruce_auto.PAUSA * 10, lambda x: f"{x / 10:.1f} s", lambda x: {"PAUSA": x / 10.0},
             "Cuánto espera el crossfader en el 50 % (los dos temas suenan a la par). 0 = sin pausa."),
            ("zmin", "Frena desde:", am, 1, 25, lambda: cruce_auto.ZONA_MIN * 100, lambda x: f"{x} %", lambda x: {"ZONA_MIN": x / 100.0},
             "Cuánto antes del cruce empieza a frenar el crossfader (% del recorrido). Más = frena desde más lejos, más suave."),
            ("zacel", "Acelera hasta:", am, 10, 100, lambda: cruce_auto.ZONA_ACEL * 100, lambda x: f"{x} %", lambda x: {"ZONA_ACEL": x / 100.0},
             "Después del cruce el crossfader sigue lento y acelera de a poco. 100 % = recién recupera la velocidad al llegar al 50 %; menos = la recupera antes."),
            ("zv", "Frenado:", "#ffffff", 10, 80, lambda: cruce_auto.ZONA_V * 10, lambda x: f"×{x / 10:.1f}", lambda x: {"ZONA_V": x / 10.0, "ZONA_K": 10.0},
             "Cuánto frena en el cruce. ×1 = parejo, sin frenado · más = el arranque pasa más rápido y el cruce queda casi quieto."),
        )
        self._fino = {}
        tit = QLabel("🎚 Ajuste fino"); tit.setStyleSheet(f"color:{TEMA['cian']}; font-weight:bold;")
        gl.addWidget(tit, 0, 0, 1, 3)
        for r, (k, texto, color, lo, hi, leer, fmt, guardar_v, tip) in enumerate(filas, 1):
            l = QLabel(texto); l.setStyleSheet(f"color:{color}; font-weight:bold;"); l.setToolTip(tip)
            sl = QSlider(Qt.Orientation.Horizontal); sl.setRange(lo, hi); sl.setToolTip(tip); sl.setValue(int(round(leer())))
            lv = QLabel(fmt(sl.value())); lv.setMinimumWidth(42); lv.setAlignment(Qt.AlignmentFlag.AlignRight)
            sl.valueChanged.connect(lambda x, g=guardar_v: self.auto_set(g(x)))
            gl.addWidget(l, r, 0); gl.addWidget(sl, r, 1); gl.addWidget(lv, r, 2)
            gl.setColumnStretch(1, 1)
            self._fino[k] = (l, sl, lv, leer, fmt)
        gl.setRowStretch(len(filas) + 1, 1)
        return marco

    def _fino_sincronizar(self):
        """Deja los deslizadores de «Ajuste fino» con los valores de ahora (los cambió el dibujo, «Valores por defecto» o la estrategia)."""
        e = _estrategia_activa()
        for k, (l, sl, lv, leer, fmt) in getattr(self, "_fino", {}).items():
            sl.blockSignals(True); sl.setValue(int(round(leer()))); sl.blockSignals(False)
            lv.setText(fmt(sl.value()))
            for w in (l, sl, lv):
                w.setEnabled(e != 1)                       # la Pro va pareja: sin pausa ni frenado

    def auto_set(self, valores):
        """Cambia valores del cruce desde el dibujo de arriba, los guarda y deja los deslizadores que quedan al día."""
        cruce_auto.aplicar(valores)
        guardar()
        for k, (sl, val, div, fmt) in self._sl.items():
            sl.blockSignals(True); sl.setValue(int(round(getattr(cruce_auto, k) * div))); sl.blockSignals(False)
            val.setText(fmt.format(sl.value() / div))
        self._fino_sincronizar()
        try:
            self.v.panel_cruce.refrescar_pausa()                # «Pausa en» / «Pausa dura» del panel del frente
        except AttributeError:
            pass
        for nombre in ("vista", "vista_nueva", "vista_inc"):
            v = getattr(self, nombre, None)
            if v is not None:
                v.update()
        if hasattr(self, "panel_nuevo"):
            self.panel_nuevo.sincronizar()
        if "AUTO_SEG" in valores or "ESTRATEGIA" in valores:
            try:
                self.v.spin_mix.setEnabled(self.v._spin_mix_activo()); self.v.btn_mix.setText(self.v._texto_btn_mix())
            except Exception:
                pass

    def _inc_sincronizar(self):
        self.chk_inc.blockSignals(True); self.chk_inc.setChecked(cruce_auto.INC_ACTIVO >= 0.5); self.chk_inc.blockSignals(False)
        self.sp_inc.blockSignals(True); self.sp_inc.setValue(int(round(cruce_auto.INC_BPM * 100))); self.sp_inc.blockSignals(False)
        self.vista_inc.update()

    def _visibles(self):
        """Esconde los ajustes que no sirven con la estrategia elegida (y los grupos que quedan vacíos)."""
        e = _estrategia_activa()
        for k, ws in self._filas_w.items():
            ok = SOLO_ESTRATEGIA.get(k, e) == e
            for w in ws:
                w.setVisible(ok)
        for caja, claves in self._cajas:
            caja.setVisible((not claves) or any(SOLO_ESTRATEGIA.get(k, e) == e for k in claves))
        self._fino_sincronizar()
        nuevo = (e == 2)                                  # solo «como mi manual» usa el panel nuevo
        self.panel_nuevo.setVisible(nuevo)
        self._cont_viejo.setVisible(not nuevo)               # el ritmo del cruce MANUAL ya no se configura acá: «Pausa en» y «Pausa dura» están en el panel «Forma de cruce A/B»
        self._info.setText("Esta es la forma de cruce PROPIA del Auto (no cambia el cruce manual): es la base de cada mezcla automática. "
                           "El Auto parte de acá y ajusta por su cuenta la duración (en compases) y la línea de compás donde pasa el bajo. "
                           "Todo se guarda solo y vale desde la próxima mezcla." if nuevo else self._texto_info_viejo)
        self.panel_nuevo.sincronizar()

    def _cambio(self, k, x, div, fmt, val):
        val.setText(fmt.format(x / div))
        cruce_auto.aplicar({k: x / div})
        if k == "EST_ESTRELLAS":
            try:
                self.v.biblioteca.refrescar_estadisticas()
            except (AttributeError, RuntimeError):
                pass
        if k == "ESTRATEGIA":
            self._visibles()
        if k in ("ESTRATEGIA", "AUTO_SEG"):
            try:
                self.v.spin_mix.setEnabled(self.v._spin_mix_activo()); self.v.btn_mix.setText(self.v._texto_btn_mix())
            except Exception:
                pass
        guardar()
        self.vista.update()
        self.panel_nuevo.sincronizar()
        if hasattr(self, "vista_inc"):
            self.vista_inc.update()
        self._actualizar_barra()

    def refrescar(self):
        """El tilde «Mezclar en break» del frente cambió BREAK_ACTIVO: deja la casilla de este diálogo igual."""
        try:
            sl, val, div, fmt = self._sl["BREAK_ACTIVO"]
            sl.blockSignals(True); sl.setValue(int(round(cruce_auto.BREAK_ACTIVO * div))); sl.blockSignals(False)
            self._visibles()
        except (KeyError, AttributeError):
            pass

    def _actualizar_barra(self):
        """Repinta la barra del auto-enganche y el timeline (la línea de trazos de «Vigilar desde» y las líneas rojas de «Qué tan bajo es un break» dependen de estos ajustes)."""
        try:
            chk = self.v.chk_break                          # el tilde del frente sigue a este ajuste
            chk.blockSignals(True); chk.setChecked(cruce_auto.BREAK_ACTIVO >= 0.5); chk.blockSignals(False)
        except AttributeError:
            pass
        try:
            self.v.barra_prog.update()
            self.v.timeline.redraw()                       # las dos líneas rojas de «Qué tan bajo es un break» también dependen de estos ajustes
        except AttributeError:
            pass

    def _abrir_datos(self):
        """Abre la carpeta de datos permanente en el Explorador."""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(CACHE_DIR))
            else:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(CACHE_DIR)))
        except OSError as e:
            self.b_datos.setText("📂 No se pudo abrir")
            self.b_datos.setToolTip(str(e))
            QTimer.singleShot(2500, lambda: self.b_datos.setText("📂 Abrir carpeta de datos"))

    def _borrar_estrellas(self):
        """Pone en cero las reproducciones de todos los temas (con confirmación); los 👍 quedan."""
        r = QMessageBox.question(self, "AutoMix — borrar estrellas",
                                 "Se pone en cero el conteo de reproducciones de TODOS los temas, así que las estrellas empiezan de nuevo.\n"
                                 "Los 👍 se mantienen. No se puede deshacer.\n\n¿Borrar?",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        n = EST.borrar_reproducciones()
        try:
            self.v.biblioteca.refrescar_estadisticas()
        except (AttributeError, RuntimeError):
            pass
        self.b_est_borrar.setText(f"🗑 Borradas ✔ ({n} temas)")
        QTimer.singleShot(2500, lambda: self.b_est_borrar.setText("🗑 Borrar todas las estrellas (poner el conteo en cero)"))

    def _olvidar(self):
        aprendizaje.olvidar()
        self.b_olv.setText("🧠 Olvidado ✔")
        QTimer.singleShot(1500, lambda: self.b_olv.setText("🧠 Olvidar lo aprendido"))

    def _defecto(self):
        for k, (sl, val, div, fmt) in self._sl.items():
            sl.blockSignals(True); sl.setValue(int(round(cruce_auto.DEFECTOS[k] * div))); sl.blockSignals(False)
            val.setText(fmt.format(sl.value() / div))
        cruce_auto.aplicar(cruce_auto.DEFECTOS)
        guardar()
        self._visibles()
        self._actualizar_barra()
        self.vista.update()
        self.panel_nuevo.sincronizar()
        try:
            self.v.panel_cruce.refrescar_pausa()
        except AttributeError:
            pass
        if hasattr(self, "vista_inc"):
            self._inc_sincronizar()

