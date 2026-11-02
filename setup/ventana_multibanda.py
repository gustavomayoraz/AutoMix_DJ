"""AutoMix — panel de multibanda y ecualizador inteligente (controles y ajustes)."""
import sys
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout
from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt
from setup.base import _log_diferido
from setup.config import DEF_AGUDOS, DEF_GOLPE, DEF_MAN_COMP, DEF_MAN_RATIO, DEF_MAN_UMBRAL, DEF_MARCA, DEF_MB_INT, DEF_MB_LIM, DEF_MB_VEL, DEF_MEDIOS, DEF_VOLUMEN, DEF_ZOOM, guardar_config, leer_config
from setup.teclas import FlechasSobreSlider, TeclaGlobalF2, TeclasGlobalesVolumen
from setup.tema import TEMA
from setup.widgets import EtiquetaAncha, EtiquetaElidida, BotonConfig, VuMeter, qss_marco


class MultibandaMixin:
    """Panel de multibanda y ecualizador inteligente (controles y ajustes) (parte de la ventana principal)."""


    def _crear_panel_multibanda(self):
        cfg = leer_config()
        w = QGroupBox("🎛️ Multibanda Automática y Ecualizador Inteligente")
        w.setStyleSheet(qss_marco("QGroupBox", TEMA["violeta"]))
        g = QGridLayout(w)
        g.setContentsMargins(8, 6, 8, 4)
        g.setVerticalSpacing(3)
        g.setHorizontalSpacing(8)
        naranja = f"font-weight: bold; color: {TEMA['ambar']};"
        suave = f"color: {TEMA['suave']};"

        fila = QHBoxLayout()
        self.chk_mb = QCheckBox("Automática (recomendado)")
        self.chk_mb.setChecked(True)          # siempre arranca en automático
        self.chk_mb.setToolTip("Tildada: todo se ajusta solo. Destildada: manual, vos movés la Compensación de cada banda (Umbral y Ratio siguen solos)")
        fila.addSpacing(12)
        fila.addWidget(QLabel("Intensidad"))
        self.sld_mb = QSlider(Qt.Orientation.Horizontal)
        self.sld_mb.setRange(0, 100)
        self.sld_mb.setValue(int(cfg.get("mb_int", DEF_MB_INT)))
        self.lbl_mb = EtiquetaAncha([f"{v} %" for v in range(0, 101)], f"{self.sld_mb.value()} %")
        self.lbl_mb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        fila.addWidget(self.sld_mb, 1); fila.addWidget(self.lbl_mb); fila.addStretch(1)
        self.chk_mb.setText("Automática")                     # (la ventana ⚙ Ajustes ya no existe: todo se maneja desde este panel)
        self.chk_mb.setStyleSheet(f"font-weight: bold; color: {TEMA['ambar']};")
        self.chk_mb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_mb_defecto = QPushButton("Valores por defecto")
        self.btn_mb_defecto.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_mb_defecto.setToolTip("Vuelve a automático y deja todos los ajustes de la multibanda como vienen de fábrica")
        self.btn_mb_defecto.clicked.connect(self._mb_ajustes_defecto)
        fila.addWidget(self.chk_mb); fila.addSpacing(8); fila.addWidget(self.btn_mb_defecto); fila.addSpacing(4)
        col = QVBoxLayout(); col.setSpacing(2); col.addLayout(fila)
        self.lbl_mb_info = EtiquetaElidida("")
        self.lbl_mb_info.setStyleSheet(suave); col.addWidget(self.lbl_mb_info)
        g.addLayout(col, 0, 0, 1, 11)

        columnas = (
            ("Nivel", -60, 0, lambda v: f"{v} dB", "Nivel medio"),
            ("Umbral", -60, 0, lambda v: f"{v} dB", "Umbral"),
            ("Ratio", 10, 30, lambda v: "1:1" if v <= 10 else f"{v / 10:.1f}:1", "Ratio"),
            ("Reducción", 0, 120, lambda v: "0.0 dB" if v == 0 else f"-{v / 10:.1f} dB", "Reducción"),
            ("Compensación", 0, 40, lambda v: f"+{v / 10:.1f} dB", "Compensación"),
        )
        ayudas = (
            "NIVEL (solo se mira)\nCuánto suena esa banda ahora, en promedio. Lo mide el programa: no se regula.\n"
            "Sirve de referencia para ver dónde está el Umbral.",
            "UMBRAL (lo calcula el programa, también en manual)\nEs la altura a partir de la cual la banda empieza a comprimirse: lo que pasa de ese nivel se baja.\n"
            "• Más bajo (ej. −40 dB): comprime casi todo, suena más parejo y 'apretado'.\n"
            "• Más alto (ej. −10 dB): comprime solo los picos fuertes, suena más natural.\n"
            "Si el Nivel queda muy por encima del Umbral, comprime mucho.",
            "RATIO (lo calcula el programa, también en manual)\nCuánto se baja lo que pasa del Umbral.\n"
            "• 1:1 = nada.  2:1 = suave (por cada 2 dB que pasa, deja pasar 1).  4:1 = fuerte.  8:1 = casi un tope.\n"
            "Más ratio = más control de los picos, pero menos pegada y menos vida.",
            "REDUCCIÓN (solo se mira)\nCuántos dB está bajando la banda en este momento por la compresión.\n"
            "• 0 dB: no está actuando.  • 1 a 3 dB: normal y casi inaudible.  • 6 dB o más: está apretando mucho (el tema se siente apagado).\n"
            "Se ajusta con el Umbral y el Ratio.",
            "COMPENSACIÓN (en manual se regula)\nGanancia que se suma a la banda después de comprimirla, para recuperar el volumen que se perdió.\n"
            "• 0 dB: sin ayuda.  • +1 a +3 dB: devuelve cuerpo cuando la reducción es alta.\n"
            "Subirla mucho hace sonar más fuerte esa banda (graves más pesados, agudos más brillantes).")
        pos_col = (0, 2, 3, 1, 4)           # lugar en pantalla de cada columna: a la izquierda las que solo se miran (Nivel, Reducción) y a la derecha las que en manual se mueven (Umbral, Ratio, Compensación)
        for j, (nombre, _mn, _mx, _f, tip) in enumerate(columnas):
            e = EtiquetaElidida(nombre); e.setStyleSheet(suave); e.setToolTip(ayudas[j])
            g.addWidget(e, 1, 1 + 2 * pos_col[j], 1, 2)
        rango_man = (0, 0, 80, 120, 120)        # en manual el Ratio llega a 8:1 y la Compensación a +12 dB: el ancho cubre el máximo
        man = (cfg.get("mb_man_umbral", list(DEF_MAN_UMBRAL)), cfg.get("mb_man_ratio", list(DEF_MAN_RATIO)), cfg.get("mb_man_comp", list(DEF_MAN_COMP)))
        self._mb_man_vals = [[int(man[0][i]), int(man[1][i]), int(man[2][i])] for i in range(3)]
        self.mb_medidores = []
        for i, banda in enumerate(("Graves", "Medios", "Agudos")):
            col_banda = {"Graves": TEMA["magenta"], "Medios": TEMA["ambar"], "Agudos": TEMA["cian"]}[banda]
            cab = QLabel(banda); cab.setStyleSheet(f"font-weight: bold; color: {col_banda};"); cab.setMinimumWidth(48)
            g.addWidget(cab, 2 + i, 0)
            fila_m = []
            for j, (nombre, mn, mx, fmt, tip) in enumerate(columnas):
                sl = QSlider(Qt.Orientation.Horizontal)
                sl.setRange(mn, mx); sl.setValue(mn); sl.setMinimumWidth(50)
                sl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                sl.setFocusPolicy(Qt.FocusPolicy.NoFocus); sl.setToolTip(tip)
                sl.setStyleSheet(f"QSlider::sub-page:horizontal {{ background: {col_banda}; border-radius:4px; }} QSlider::handle:horizontal {{ border-color: {col_banda}; }}")
                lbl = EtiquetaAncha([fmt(v) for v in range(mn, max(mx, rango_man[j]) + 1)], fmt(sl.value())); lbl.setStyleSheet(suave)
                sl.valueChanged.connect(lambda v, l=lbl, f=fmt: l.setText(f(v)))
                if j == 4:                              # Compensación: en manual la mueve el usuario acá mismo (Umbral y Ratio siguen automáticos)
                    sl.valueChanged.connect(self._mb_manual_cambio)
                g.addWidget(sl, 2 + i, 1 + 2 * pos_col[j]); g.addWidget(lbl, 2 + i, 2 + 2 * pos_col[j])
                fila_m.append(sl)
            self.mb_medidores.append(fila_m)
        self.mb_man = self.mb_medidores             # (los mismos deslizadores: indicadores en automático, editables en manual)

        linea = QFrame(); linea.setFrameShape(QFrame.Shape.HLine); linea.setFrameShadow(QFrame.Shadow.Sunken)
        g.addWidget(linea, 5, 0, 1, 11)
        tit = QLabel("Avanzado"); tit.setStyleSheet(naranja)
        g.addWidget(tit, 6, 0, 1, 3)
        def deslizador(fila_g, texto, mn, mx, valor, fmt, tip):
            et = QLabel(texto); et.setToolTip(tip)
            sl = QSlider(Qt.Orientation.Horizontal); sl.setRange(mn, mx); sl.setValue(int(valor)); sl.setToolTip(tip)
            lb = EtiquetaAncha([fmt(v) for v in range(mn, mx + 1)], fmt(sl.value()))
            lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            sl.valueChanged.connect(lambda v, l=lb, f=fmt: l.setText(f(v)))
            lb.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            g.addWidget(et, fila_g, 0, 1, 2); g.addWidget(sl, fila_g, 2, 1, 4); g.addWidget(lb, fila_g, 6, 1, 1)
            return sl
        self.sld_lim = deslizador(7, "Máx. corrección", 1, 10, cfg.get("mb_lim", DEF_MB_LIM), lambda v: f"±{v} dB", "Máx corrección EQ")
        self.sld_vel = deslizador(8, "Velocidad", 1, 40, cfg.get("mb_vel", DEF_MB_VEL), lambda v: f"{v / 10:.1f} dB/s", "Velocidad EQ")

        self.sld_golpe = deslizador(9, "Golpe 43 Hz", 0, 100, cfg.get("golpe_43", DEF_GOLPE), lambda v: "apagado" if v == 0 else f"{v} %",
                                    "Realza el bombo y el bajo (zona de 43 Hz): empujón fijo + extra en el ataque de cada golpe + armónicos para parlantes chicos. 0 % = apagado")
        self.sld_golpe.setStyleSheet("")
        self._mb_manual = False
        self._mb_pintando = False
        self.sld_medios = deslizador(10, "Medios (1 kHz)", -100, 60, int(cfg.get("medios_db10", DEF_MEDIOS)),
                                     lambda v: "0 dB" if v == 0 else f"{v / 10:+.1f} dB",
                                     "Baja (−) o sube (+) los medios con una campana ancha en 1 kHz. Para sacar solo un poquito: probá −1,0 a −2,0 dB. 0 = sin efecto")
        self.sld_agudos = deslizador(11, "Agudos (6 kHz)", -100, 60, int(cfg.get("agudos_db10", DEF_AGUDOS)),
                                     lambda v: "0 dB" if v == 0 else f"{v / 10:+.1f} dB",
                                     "Baja (−) o sube (+) los agudos (brillo, desde ~6 kHz). Para sacar solo un poquito: probá −1,0 a −2,0 dB. 0 = sin efecto")
        # Mezclar / Duración / Volumen general: a la derecha de los deslizadores (más compacto)
        der = QVBoxLayout(); der.setSpacing(6)
        self.btn_mix.setFixedHeight(34)
        self.btn_mix.setFixedHeight(38)
        self.btn_mix.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffd84a, stop:1 #ff9a00); color:#2a1700; font-weight:bold;"
            " border:1px solid #ffe08a; border-radius:11px; }"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffe473, stop:1 #ffae2e); }"
            "QPushButton:pressed { background:#d98400; padding-top:2px; }"
            "QPushButton:disabled { background:#3a3322; color:#7a7155; border-color:#4a422c; }")
        _glow = QGraphicsDropShadowEffect(self.btn_mix); _glow.setBlurRadius(26); _glow.setOffset(0, 2); _glow.setColor(QColor(255, 160, 0, 130))
        self.btn_mix.setGraphicsEffect(_glow)
        # Tarjeta 🎬 Mezcla (ámbar): loop, duración y el botón Mezclar grande
        self.marco_mezcla = QGroupBox("🎬 Mezcla")
        self.marco_mezcla.setObjectName("marcoMezcla")
        self.marco_mezcla.setStyleSheet(qss_marco("QGroupBox#marcoMezcla", TEMA["ambar"]))
        col_mix = QVBoxLayout(self.marco_mezcla); col_mix.setContentsMargins(10, 6, 10, 8); col_mix.setSpacing(6)
        col_mix.addWidget(self._crear_loop())     # 🔁 Loop + largo (¼ ½ 1 2)
        # la duración va ahora en la barra de arriba (junto a las teclas globales): número grande y bien legible
        _f = QFont(self.spin_mix.font()); _f.setPointSizeF(14.0); _f.setBold(True); self.spin_mix.setFont(_f)
        self.spin_mix.setFixedSize(104, 36)
        self.spin_mix.setStyleSheet("QSpinBox { font-size: 19px; font-weight: bold; color: #ffffff; padding: 0px 22px 0px 8px; }")
        _fl = QFont(self.lbl_dur.font()); _fl.setPointSizeF(11.0); _fl.setBold(True); self.lbl_dur.setFont(_fl)
        self.lbl_dur.setFixedHeight(36)
        self.btn_mix.setMinimumWidth(0)
        self.btn_mix.setMinimumHeight(38); self.btn_mix.setMaximumHeight(16777215)
        self.btn_mix.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)      # Ignored: el largo del texto (que cambia entre Manual y Auto) no ensancha la tarjeta ni mueve el resto
        col_mix.addWidget(self.btn_mix, 1)
        self._fila_herr.addWidget(self.marco_mezcla, 1, 1)
        # barra de iconos (arriba de las tarjetas): teclas globales a la izquierda de 🖥 ↖ ⚙
        self.chk_f2g = QCheckBox("⌨ Teclas globales")
        self.chk_f2g.setToolTip("Con esto activado, F2 (mezclar) y las flechas ↑ / ↓ (volumen) funcionan aunque estés en otra ventana o programa (mientras AutoMix esté abierto).\n"
                                "Mientras esté activado, F2 y las flechas ↑ / ↓ no llegan a los otros programas (por ejemplo, no mueven el cursor ni las listas fuera de AutoMix).")
        self.chk_f2g.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.f2_global = TeclaGlobalF2(self._mix_clicked)
        self.vol_global = TeclasGlobalesVolumen(self._vol_tecla)
        self.flechas_slider = FlechasSobreSlider(); self.flechas_slider.instalar()      # ← / → mueven el slider que esté bajo el mouse
        if sys.platform != "win32":
            self.chk_f2g.setVisible(False)
        self.chk_f2g.setChecked(bool(leer_config().get("f2_global", True)))
        self.chk_f2g.toggled.connect(self._f2_global_cambio)
        self._f2_global_cambio(self.chk_f2g.isChecked(), guardar=False)
        f_dur = self._fila_duracion
        f_dur.addWidget(self.lbl_dur)             # Duración (s): en la misma línea que las teclas globales
        f_dur.addWidget(self.spin_mix)
        f_dur.addStretch(1)                       # los tres grupos (duración · teclas globales · iconos) quedan repartidos en el ancho, sin apretarse
        f_dur.addWidget(self.chk_f2g)
        f_dur.addStretch(1)
        f_dur.addSpacing(0)                       # (marcador: los botones 🖥 ↖ ⚙ se insertan antes de este último elemento)
        self._f_dur = f_dur
        f_vol = QHBoxLayout(); f_vol.addWidget(QLabel("🔊 Volumen")); f_vol.addWidget(self.sld_master, 1); f_vol.addWidget(self.lbl_master)
        self.crossfader.setToolTip("Crossfader A ↔ B (volumen + BPM compartido)")
        der.addLayout(self._cf_row)
        self.vu_meter = VuMeter(lambda: self.mixer.vu)
        der.addWidget(self.vu_meter)
        der.addStretch(1)
        der.addLayout(f_vol)                      # el volumen va debajo del fader, a la altura de la fila de Agudos
        g.addLayout(der, 6, 7, 6, 4)
        self.chk_mb.toggled.connect(self._mb_cambio)
        for sl in (self.sld_mb, self.sld_lim, self.sld_vel, self.sld_golpe, self.sld_medios, self.sld_agudos):
            sl.valueChanged.connect(self._mb_cambio)
        self._mb_cambio()
        self._mb_grid = g
        self._mb_compacto_on = False
        return w

    def _mb_compacto(self, activo):
        """Pantallas bajas (ej. 1366x768): oculta la tabla de bandas del panel (Nivel · Umbral · Ratio · Reducción · Compensación)
        mientras es solo un indicador (modo automático). En manual se muestra, porque ahí se mueven los deslizadores de la tabla."""
        self._mb_compacto_on = bool(activo)
        self._mb_aplicar_compacto()

    def _mb_aplicar_compacto(self):
        g = getattr(self, "_mb_grid", None)
        if g is None:
            return
        ocultar = bool(getattr(self, "_mb_compacto_on", False)) and not self._mb_manual
        for i in range(g.count()):
            wd = g.itemAt(i).widget()
            if wd is not None and 1 <= g.getItemPosition(i)[0] <= 5:       # filas 1 a 5: títulos, 3 bandas y la línea
                wd.setVisible(not ocultar)
        g.invalidate()
        p = g.parentWidget()                    # el tamaño mínimo se guarda en cada contenedor: se avisa hacia arriba hasta la ventana
        while p is not None:
            p.updateGeometry()
            p = p.parentWidget()

    def _mb_ajustes_defecto(self):
        """Vuelve a automático y deja todos los ajustes de la multibanda como vienen de fábrica."""
        for sl, v in ((self.sld_mb, DEF_MB_INT), (self.sld_lim, DEF_MB_LIM), (self.sld_vel, DEF_MB_VEL), (self.sld_golpe, DEF_GOLPE),
                      (self.sld_medios, DEF_MEDIOS), (self.sld_agudos, DEF_AGUDOS)):
            sl.setValue(int(v))
        self._mb_man_vals = [[int(DEF_MAN_UMBRAL[i]), int(DEF_MAN_RATIO[i]), int(DEF_MAN_COMP[i])] for i in range(3)]
        mb = self.mixer.mb.mb
        for i, (u, r, c) in enumerate(self._mb_man_vals):
            mb.man_umbral[i] = float(u); mb.man_ratio[i] = r / 10.0; mb.man_comp[i] = c / 10.0
        guardar_config(mb_man_umbral=list(DEF_MAN_UMBRAL), mb_man_ratio=list(DEF_MAN_RATIO), mb_man_comp=list(DEF_MAN_COMP))
        if self._mb_manual:
            self.chk_mb.setChecked(True)                # vuelve a automático
        self._set_status("⚙ Multibanda: ajustes por defecto (automática)")

    def _mb_modo_manual(self, manual):
        """En manual solo la Compensación de cada banda se mueve a mano; Umbral y Ratio siguen automáticos. En automático todo son indicadores."""
        if manual == self._mb_manual:
            return
        self._mb_manual = manual
        self._mb_pintando = True
        try:
            for i, fila_d in enumerate(self.mb_man):
                sl = fila_d[4]
                sl.setRange(0, 120 if manual else 40)       # en manual la Compensación llega a +12 dB
                sl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not manual)
                sl.setFocusPolicy(Qt.FocusPolicy.StrongFocus if manual else Qt.FocusPolicy.NoFocus)
                if manual:
                    sl.setValue(int(self._mb_man_vals[i][2]))
        finally:
            self._mb_pintando = False
        self.lbl_mb_info.setText("Manual: movés la Compensación de cada banda · Umbral y Ratio siguen solos · cruces fijos 150 Hz / 2,5 kHz" if manual
                                 else "Cruces fijos: 150 Hz / 2,5 kHz · los valores de abajo se mueven solos, en vivo")
        self._mb_aplicar_compacto()

    def _mb_manual_cambio(self, *_):
        if self._mb_pintando or not self._mb_manual:
            return
        mb = self.mixer.mb.mb
        for i, fila_d in enumerate(self.mb_man):
            c = fila_d[4].value()
            self._mb_man_vals[i][2] = c
            mb.man_comp[i] = c / 10.0
        guardar_config(mb_man_comp=[v[2] for v in self._mb_man_vals])

    def _mb_cambio(self, *_):
        self.lbl_mb.setText(f"{self.sld_mb.value()} %")
        _log_diferido("mb", "multibanda", f"intensidad {self.sld_mb.value()} % · máx {self.sld_lim.value()} dB · vel {self.sld_vel.value() / 10:.1f} dB/s · "
                      f"golpe {self.sld_golpe.value()} % · medios {self.sld_medios.value()} dB · agudos {self.sld_agudos.value()} dB")
        mb = self.mixer.mb
        mb.intensidad = self.sld_mb.value() / 100.0
        mb.eq.limite = float(self.sld_lim.value())
        mb.eq.velocidad = self.sld_vel.value() / 10.0
        manual = not self.chk_mb.isChecked()
        self._mb_modo_manual(manual)
        mb.mb.manual = manual
        if manual:
            self._mb_manual_cambio()
        self.sld_mb.setEnabled(True); self.sld_lim.setEnabled(True); self.sld_vel.setEnabled(True)     # también activos en manual
        self.mixer.mb_activo = True
        self.mixer.golpe.cantidad = self.sld_golpe.value() / 100.0
        self.mixer.medios.db = self.sld_medios.value() / 10.0
        self.mixer.agudos.db = self.sld_agudos.value() / 10.0
        guardar_config(medios_db10=int(self.sld_medios.value()), agudos_db10=int(self.sld_agudos.value()), mb_auto=self.chk_mb.isChecked(),
                       mb_int=int(self.sld_mb.value()),
                       mb_lim=int(self.sld_lim.value()), mb_vel=int(self.sld_vel.value()),
                       golpe_43=int(self.sld_golpe.value()))

    def _mb_actualizar_barras(self):
        mb = self.mixer.mb.mb
        act = self.mixer.mb_activo
        self._mb_pintando = True
        try:
            for i, fila in enumerate(self.mb_medidores):
                vals = (mb.niveles[i], mb.umbral[i], mb.ratio[i] * 10.0, mb.reduccion[i] * 10.0, mb._mk[i] * 10.0) \
                    if act else (-60, -60, 10, 0, 0)
                for j, (sl, v) in enumerate(zip(fila, vals)):
                    if self._mb_manual and j == 4:              # en manual la Compensación la mueve el usuario
                        continue
                    sl.setValue(int(round(float(v))))
        finally:
            self._mb_pintando = False

    def aplicar_valores_defecto(self):
        """Pone los controles en sus valores por defecto (multibanda, tono, volumen, zoom y marca del auto-enganche)."""
        for sl, v in ((self.sld_mb, DEF_MB_INT), (self.sld_lim, DEF_MB_LIM), (self.sld_vel, DEF_MB_VEL), (self.sld_golpe, DEF_GOLPE),
                      (self.sld_medios, DEF_MEDIOS), (self.sld_agudos, DEF_AGUDOS), (self.sld_master, DEF_VOLUMEN), (self.sld_zoom, DEF_ZOOM)):
            sl.setValue(int(v))
        try:
            self.barra_prog.set_marca(DEF_MARCA)
            self._marca_cambiada(DEF_MARCA)
        except Exception:
            import traceback; traceback.print_exc()

    def _vol_general_cambio(self, v):
        _log_diferido("vol", "volumen", f"general {v} %")
        self.lbl_master.setText(f"{v}%")
        self.mixer.set_volumen(v / 100.0)
        guardar_config(vol_general=int(v))
