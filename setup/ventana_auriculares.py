"""AutoMix — auriculares: botones 🎧 por deck, elección del dispositivo y volumen."""
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget
from setup.base import log
from setup.config import guardar_config, leer_config
from setup.tema import COLOR_DECK_A, COLOR_DECK_B, TEMA
from setup.widgets import qss_marco


class _ComboRefresco(QComboBox):
    """Combo que vuelve a leer los dispositivos cada vez que se despliega (por si enchufaste el DAC después de abrir)."""
    def __init__(self, al_abrir):
        super().__init__()
        self._al_abrir = al_abrir

    def showPopup(self):
        try:
            self._al_abrir()
        except Exception:
            pass
        super().showPopup()


class AuricularesMixin:
    """Auriculares (PFL): escuchar un deck por otra salida de sonido (parte de la ventana principal)."""

    def _crear_auriculares(self):
        aur = self.mixer.aur
        cfg = leer_config()
        self.marco_aur = QWidget()                      # contenedor transparente con los 2 cuadros: salida principal y auriculares
        self.marco_aur.setObjectName("contAur")
        self.marco_aur.setStyleSheet("QWidget#contAur { background:transparent; border:none; }")
        cont = QVBoxLayout(self.marco_aur); cont.setContentsMargins(0, 0, 0, 0); cont.setSpacing(6)
        marco_sal = QFrame(); marco_sal.setObjectName("marcoSal")          # cuadro 1 (cian): salida PRINCIPAL
        marco_sal.setStyleSheet(qss_marco("QFrame#marcoSal", TEMA["cian"], titulo=False))
        col_sal = QVBoxLayout(marco_sal); col_sal.setContentsMargins(8, 4, 8, 6); col_sal.setSpacing(3)
        marco_au = QFrame(); marco_au.setObjectName("marcoAur")            # cuadro 2 (violeta): AURICULARES
        marco_au.setStyleSheet(qss_marco("QFrame#marcoAur", TEMA["violeta"], titulo=False))
        col = QVBoxLayout(marco_au); col.setContentsMargins(8, 4, 8, 6); col.setSpacing(3)
        cont.addWidget(marco_sal); cont.addWidget(marco_au)
        fila1 = QHBoxLayout(); fila1.setContentsMargins(0, 0, 0, 0); fila1.setSpacing(4)
        fila1.addWidget(self._rotulo("🎧 AURICULARES", "solo para vos", "#c792ff"))
        self.btn_cue = {}                              # (los botones A/B de escucha en vivo se sacaron: se usa el play de abajo)
        fila1.addStretch(1)
        self.btn_aur_ref = QPushButton("🔄")
        self.btn_aur_ref.setFixedSize(30, 26); self.btn_aur_ref.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_aur_ref.setStyleSheet("QPushButton { padding:0px; background:rgba(0,0,0,70); border:1px solid #888; border-radius:7px; }"
                                       "QPushButton:hover { background:rgba(255,255,255,30); }")
        self.btn_aur_ref.setToolTip("Buscar de nuevo los dispositivos de audio (para auriculares Bluetooth o un DAC conectado después de abrir el programa).\n"
                                    "Corta el sonido un instante.")
        self.btn_aur_ref.clicked.connect(self._aur_buscar_de_nuevo)
        fila1.addWidget(self.btn_aur_ref)
        col.addLayout(fila1)
        # Play propio de los auriculares: suena SOLO por ahí, con su propia posición (el deck no arranca, no se dispara
        # la mezcla automática y no se oye en los parlantes). La barra mueve la posición de la última previa tocada.
        aur.decks = {"A": self.deck_a, "B": self.deck_b}
        fila_p = QHBoxLayout(); fila_p.setContentsMargins(0, 0, 0, 0); fila_p.setSpacing(4)
        self.btn_prev = {}
        for deck, color in ((self.deck_a, COLOR_DECK_A), (self.deck_b, COLOR_DECK_B)):
            b = QPushButton(f"▶{deck.name}")
            b.setCheckable(True); b.setFixedSize(40, 26); b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(f"QPushButton {{ color:{color}; font-weight:bold; padding:0px; background:rgba(0,0,0,70);"
                            f" border:1px solid {color}; border-radius:7px; }}"
                            f"QPushButton:hover {{ background:rgba(255,255,255,30); }}"
                            f"QPushButton:checked {{ background:{color}; color:#0b1020; }}")
            b.setToolTip(f"Reproducir el tema cargado en el Deck {deck.name} SOLO en los auriculares.\n"
                         "No arranca el deck ni dispara la mezcla automática: tiene su propia posición.")
            b.toggled.connect(lambda on, d=deck.name: self._prev_cambio(d, on))
            self.btn_prev[deck.name] = b
            fila_p.addWidget(b)
        self.sld_prev = QSlider(Qt.Orientation.Horizontal)
        self.sld_prev.setRange(0, 1000); self.sld_prev.setFixedHeight(26)
        self.sld_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sld_prev.setToolTip("Posición de la previa de auriculares (arrastrá para saltar a otra parte del tema).")
        self.sld_prev.sliderMoved.connect(self._prev_mover)
        self._prev_act = "A"
        fila_p.addWidget(self.sld_prev, 1)
        self._prev_timer = QTimer(self); self._prev_timer.setInterval(120)
        self._prev_timer.timeout.connect(self._prev_tick)
        col_sal.addWidget(self._rotulo("🔊 SALIDA PRINCIPAL", "parlantes", "#4dd2ff"))
        self.cmb_sal = _ComboRefresco(self._sal_refrescar)          # salida PRINCIPAL (parlantes): la que se elige acá sale de la lista de auriculares
        self.cmb_sal.setFixedHeight(26)
        self.cmb_sal.setMinimumContentsLength(14)
        self.cmb_sal.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_sal.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cmb_sal.setToolTip("Salida de sonido PRINCIPAL (los parlantes o la consola). Al elegirla se saca de la lista de auriculares.\n"
                                "Al cambiarla el sonido se corta un instante.")
        self.cmb_sal.addItem(self._SAL_DEFECTO, None)
        if self.mixer.salida_pref:
            self.cmb_sal.addItem(self.mixer.salida_pref, self.mixer.salida_pref)
            self.cmb_sal.setCurrentIndex(1)
        self.cmb_sal.currentIndexChanged.connect(self._sal_cambio)
        self._estilo_lista(self.cmb_sal, "#1f4f66", "#4dd2ff")
        col_sal.addWidget(self.cmb_sal)
        self.cmb_aur = _ComboRefresco(self._aur_refrescar)
        self.cmb_aur.setFixedHeight(26)
        self.cmb_aur.setMinimumContentsLength(14)
        self.cmb_aur.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_aur.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cmb_aur.setToolTip("Salida de sonido de los auriculares (por ejemplo tu DAC USB).")
        self._estilo_lista(self.cmb_aur, "#4a2f73", "#c792ff")
        col.addWidget(self.cmb_aur)
        fila3 = QHBoxLayout(); fila3.setContentsMargins(0, 0, 0, 0); fila3.setSpacing(6)
        fila3.addWidget(QLabel("🎧"))
        self.sld_aur = QSlider(Qt.Orientation.Horizontal)
        self.sld_aur.setRange(0, 100); self.sld_aur.setFixedHeight(26)
        self.sld_aur.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sld_aur.setValue(int(cfg.get("aur_vol", 80)))
        aur.vol = self.sld_aur.value() / 100.0
        self.lbl_aur = QLabel(f"{self.sld_aur.value()} %")
        self.sld_aur.valueChanged.connect(self._aur_vol_cambio)
        fila3.addWidget(self.sld_aur, 1); fila3.addWidget(self.lbl_aur)
        col.addLayout(fila3)
        col.addWidget(self._rotulo("▶ PREVIA", "probar un deck en los auriculares", "#c792ff"))
        col.addLayout(fila_p)
        self._fila_herr.addWidget(self.marco_aur, 1, 0)
        self._aur_nombre_pref = cfg.get("aur_nombre", "")
        # Arranque rápido: NO se consulta la lista de dispositivos ni se abre la salida ahora (en Windows puede tardar
        # varios segundos). Se muestra el nombre guardado y se abre recién al tocar A/B o al desplegar la lista.
        self.cmb_aur.addItem("— sin auriculares —", None)
        if self._aur_nombre_pref:
            self.cmb_aur.addItem(self._aur_nombre_pref, self._aur_nombre_pref)
            self.cmb_aur.setCurrentIndex(1)
        self.cmb_aur.currentIndexChanged.connect(self._aur_dispositivo_cambio)

    @staticmethod
    def _estilo_lista(cmb, fondo, borde):
        """Fondo propio para la lista desplegable de un combo, para que no se confunda con el fondo del reproductor."""
        cmb.view().setStyleSheet(
            f"QAbstractItemView {{ background:{fondo}; color:#ffffff; border:2px solid {borde}; outline:0px;"
            f" selection-background-color:{borde}; selection-color:#0b1020; }}"
            f"QAbstractItemView::item {{ padding:4px 8px; min-height:22px; }}")

    @staticmethod
    def _rotulo(titulo, aclaracion, color):
        """Título chico sobre cada lista de salidas, para no confundir la principal con la de auriculares."""
        lb = QLabel(f"<span style='color:{color}'><b>{titulo}</b></span> <span style='color:#9aa3b5'>· {aclaracion}</span>")
        lb.setStyleSheet("font-size:12px; background:transparent; border:none; padding:0px; margin:2px 0px 0px 0px;")
        lb.setTextFormat(Qt.TextFormat.RichText)
        return lb

    _SAL_DEFECTO = "🔊 Principal: la predeterminada de Windows"

    def _sal_refrescar(self):
        """Lista todas las salidas estéreo que detecta Windows (también la del DAC USB), manteniendo la elegida."""
        dev = [x for x in self.mixer.aur.listar() if x[2] >= 2]
        actual = self.mixer.salida_pref
        bloq = self.cmb_sal.blockSignals(True)
        self.cmb_sal.clear()
        self.cmb_sal.addItem(self._SAL_DEFECTO, None)
        for idx, nombre, ch, api in dev:
            self.cmb_sal.addItem(nombre, nombre)
        i = self.cmb_sal.findData(actual) if actual else 0
        self.cmb_sal.setCurrentIndex(i if i >= 0 else 0)
        self.cmb_sal.blockSignals(bloq)

    def _sal_cambio(self, *_):
        """Cambió la salida principal: se abre en ese dispositivo y se saca de la lista de auriculares."""
        nombre = self.cmb_sal.currentData()
        if nombre == self.mixer.salida_pref:
            return
        antes = self.cmb_aur.currentData()                  # los auriculares que había elegidos
        if self.mixer.cambiar_salida(nombre):
            guardar_config(salida_principal=nombre or "")
            self._aur_refrescar()                      # (la salida principal ya no aparece en la lista de auriculares)
            log("audio", f"salida principal: {nombre or 'la predeterminada de Windows'}")
            msg = f"🔊 Salida principal: {nombre or self.mixer.nombre_salida()}"
            if antes and self.cmb_aur.currentData() is None:     # los auriculares usaban justo esa salida: pasan a «sin auriculares»
                self.mixer.aur.cerrar()
                for b in list(self.btn_cue.values()) + list(self.btn_prev.values()):
                    b.blockSignals(True); b.setChecked(False); b.blockSignals(False)
                guardar_config(aur_nombre="")
                self._aur_nombre_pref = ""
                msg += " · los auriculares usaban esa salida: quedaron sin auriculares"
            self._set_status(msg)
        else:
            self._set_status(f"⚠️ No se pudo abrir «{nombre}» como salida principal: queda la anterior")
            self._sal_refrescar()

    def _aur_refrescar(self):
        """Lee los dispositivos de salida y los pone en la lista, manteniendo la elección."""
        dev = self.mixer.aur.listar(self.mixer.nombre_salida())
        actual = self.cmb_aur.currentData()
        bloq = self.cmb_aur.blockSignals(True)
        self.cmb_aur.clear()
        self.cmb_aur.addItem("— sin auriculares —", None)
        for idx, nombre, ch, api in dev:
            self.cmb_aur.addItem(nombre, nombre)
        quiero = actual or getattr(self, "_aur_nombre_pref", "")
        i = self.cmb_aur.findData(quiero) if quiero else -1
        self.cmb_aur.setCurrentIndex(i if i >= 0 else 0)
        self.cmb_aur.blockSignals(bloq)

    def _aur_buscar_de_nuevo(self):
        """Reinicia PortAudio para que aparezcan dispositivos conectados después de abrir (Bluetooth, USB)."""
        aur = self.mixer.aur
        pref = self.cmb_aur.currentData() or self._aur_nombre_pref
        self._set_status("🔄 Buscando dispositivos de audio…")
        for b in self.btn_cue.values():
            b.setChecked(False)
        self.mixer.reiniciar_audio()
        self._aur_refrescar()
        dev = aur.listar(self.mixer.nombre_salida())
        for i, v, ch, api in dev:
            log("audio", f"salida {i}: {v} · {ch} canales · {api}")
        if pref:
            j = self.cmb_aur.findData(pref)
            if j >= 0:
                self.cmb_aur.setCurrentIndex(j)
        self._set_status(f"🔄 {len(dev)} salidas de audio encontradas — elegí tus auriculares en la lista")

    def _aur_auto_dispositivos(self):
        """Vigila (1 vez por segundo) los aparatos de audio que tiene Windows. Si se conectó o desconectó uno, espera a
        que se estabilice, reinicia PortAudio para que lo vea y actualiza la lista; si apareció un aparato nuevo (o volvió
        el que ya usabas), lo elige y abre solo. Reemplaza al botón 🔄, que queda por si hace falta."""
        ahora = time.monotonic()
        if ahora - getattr(self, "_t_dev", 0) < 1.0 or self.mixer._recuperando:
            return
        self._t_dev = ahora
        aur = self.mixer.aur
        pres = aur.nombres_presentes()
        if pres is None:
            return
        pres = frozenset(pres)
        base = getattr(self, "_dev_base", None)
        if base is None:
            self._dev_base = pres
            return
        if pres == base:
            self._dev_visto = None
            return
        if pres != getattr(self, "_dev_visto", None):
            self._dev_visto = pres                       # cambió: esperar a que Windows termine de registrarlo
            self._dev_t = ahora
            return
        if ahora - self._dev_t < 1.5:
            return
        nuevos = pres - base
        self._dev_base = pres
        self._dev_visto = None
        pref = self._aur_nombre_pref
        self.mixer.reiniciar_audio()
        self._aur_refrescar()
        dev = aur.listar(self.mixer.nombre_salida())
        for i, v, ch, api in dev:
            log("audio", f"salida {i}: {v} · {ch} canales · {api}")
        elegir = None
        if nuevos:
            cand = sorted((x for x in dev if any(aur.coincide(x[1], p) for p in nuevos)), key=lambda x: x[2] < 2)
            if cand:
                elegir = next((x[1] for x in cand if x[1] == pref), None)
                if elegir is None and not aur.activo:
                    elegir = cand[0][1]
        if elegir is None and pref and self.cmb_aur.findData(pref) >= 0:
            elegir = pref                                # reabrir lo que ya usabas si sigue estando
        if elegir:
            j = self.cmb_aur.findData(elegir)
            if j >= 0:
                if j == self.cmb_aur.currentIndex():
                    self._aur_abrir(elegir)
                else:
                    self.cmb_aur.setCurrentIndex(j)      # dispara la apertura
                return
        self._set_status("🔄 Cambió la lista de dispositivos de audio")

    def _aur_dispositivo_cambio(self, *_):
        aur = self.mixer.aur
        nombre = self.cmb_aur.currentData()
        if not nombre:
            aur.cerrar()
            for b in self.btn_cue.values():
                b.setChecked(False)
            guardar_config(aur_nombre="")
            return
        self._aur_abrir(nombre)

    def _aur_abrir(self, nombre):
        """Abre la salida de auriculares con ese nombre. Devuelve True si quedó andando."""
        aur = self.mixer.aur
        if aur.activo and aur.nombre == nombre:
            return True
        t0 = time.perf_counter()
        d = next((x for x in aur.listar(self.mixer.nombre_salida()) if x[1] == nombre), None)
        if d is not None and aur.abrir(d[0], nombre, d[2], d[3]):
            self._aur_nombre_pref = nombre
            guardar_config(aur_nombre=nombre)
            self._aur_vol_de(nombre)
            log("audio", f"auriculares: {nombre} (abrió en {time.perf_counter() - t0:.2f} s)")
            self._set_status(f"🎧 Auriculares: {nombre}")
            return True
        motivo = aur.error or "no se encontró el dispositivo (¿está conectado? probá el botón 🔄)"
        log("audio", f"auriculares: no se pudo abrir «{nombre}» ({motivo})")
        self._set_status(f"⚠️ No se pudo abrir «{nombre}» para los auriculares")
        self.cmb_aur.blockSignals(True); self.cmb_aur.setCurrentIndex(0); self.cmb_aur.blockSignals(False)
        return False

    def _cue_cambio(self, nombre_deck, activo):
        aur = self.mixer.aur
        if activo and not aur.activo:
            nombre = self.cmb_aur.currentData()
            if not (nombre and self._aur_abrir(nombre)):          # la salida guardada se abre recién ahora (primer uso)
                QTimer.singleShot(0, lambda: self._cue_revertir(nombre_deck))
                if not nombre:
                    self._set_status("🎧 Elegí primero la salida de los auriculares (en la lista de abajo)")
                return
        if nombre_deck == "A":
            aur.cue_a = bool(activo)
        else:
            aur.cue_b = bool(activo)

    def _prev_cambio(self, nom, activo):
        aur = self.mixer.aur
        if activo:
            if not aur.activo:
                nombre = self.cmb_aur.currentData()
                if not (nombre and self._aur_abrir(nombre)):
                    QTimer.singleShot(0, lambda: self._prev_revertir(nom))
                    if not nombre:
                        self._set_status("🎧 Elegí primero la salida de los auriculares (en la lista de abajo)")
                    return
            if not aur.previa_play(nom):
                QTimer.singleShot(0, lambda: self._prev_revertir(nom))
                self._set_status(f"🎧 El Deck {nom} no tiene un tema cargado")
                return
            self._prev_act = nom
            self._prev_timer.start()
        else:
            aur.previa_pausa(nom)

    def _prev_revertir(self, nom):
        b = self.btn_prev[nom]
        b.blockSignals(True); b.setChecked(False); b.blockSignals(False)

    def _prev_mover(self, v):
        self.mixer.aur.previa_ir(self._prev_act, v / 1000.0)

    def _prev_tick(self):
        aur = self.mixer.aur
        hay = False
        for nom, b in self.btn_prev.items():
            sonando = aur.prev[nom].playing
            hay = hay or sonando
            if b.isChecked() != sonando:                   # terminó el tema o se cargó otro: el botón se suelta solo
                b.blockSignals(True); b.setChecked(sonando); b.blockSignals(False)
        if not self.sld_prev.isSliderDown():
            self.sld_prev.setValue(int(aur.previa_fraccion(self._prev_act) * 1000))
        if not hay:
            self._prev_timer.stop()

    def _cue_revertir(self, nombre_deck):
        b = self.btn_cue[nombre_deck]
        b.blockSignals(True); b.setChecked(False); b.blockSignals(False)

    @staticmethod
    def _aur_clave(nombre):
        return " ".join(nombre.split(" [")[0].replace(" · mono", "").lower().split())

    def _aur_vol_cambio(self, v):
        aur = self.mixer.aur
        aur.vol = v / 100.0
        self.lbl_aur.setText(f"{v} %")
        kw = {"aur_vol": int(v)}
        if aur.activo and aur.nombre:                    # el volumen se recuerda por aparato (DAC, Bluetooth…)
            vols = dict(leer_config().get("aur_vols", {}))
            vols[self._aur_clave(aur.nombre)] = int(v)
            kw["aur_vols"] = vols
        guardar_config(**kw)

    def _aur_vol_de(self, nombre):
        """Pone el volumen que tenía este aparato la última vez (si es la primera, queda el actual y se guarda)."""
        v = leer_config().get("aur_vols", {}).get(self._aur_clave(nombre))
        if v is None:
            v = self.sld_aur.value()
        v = max(0, min(100, int(v)))
        if v != self.sld_aur.value():
            self.sld_aur.setValue(v)                     # dispara _aur_vol_cambio, que lo guarda
        else:
            self._aur_vol_cambio(v)
