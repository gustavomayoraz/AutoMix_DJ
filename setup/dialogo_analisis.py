"""AutoMix — mini ventana del análisis masivo: cuando se agregan muchos temas, bloquea el programa y analiza a máxima velocidad."""
import time
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout
from setup.tema import TEMA
from setup.widgets import EtiquetaElidida


def _fmt(n):
    return f"{n:,}".replace(",", ".")


def _tiempo(seg):
    seg = int(max(0, seg))
    h, r = divmod(seg, 3600); m, s = divmod(r, 60)
    return f"{h} h {m:02d} min" if h else (f"{m} min {s:02d} s" if m else f"{s} s")


class DialogoAnalisis(QDialog):
    cancelar = Signal()
    segundo_plano = Signal()

    def __init__(self, parent, total, procesos=0, permitir_fondo=True, base=0, pie=None, volver=None):
        super().__init__(parent)
        self._base = max(0, base)          # temas que ya estaban analizados antes de esta tanda (el avance se muestra sobre el total de la lista)
        self.setWindowTitle("AutoMix 🎚️ — Analizando biblioteca")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowMinimizeButtonHint)       # con botón de minimizar
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedSize(480, 275)
        T = TEMA
        self.setStyleSheet(
            f"QDialog {{ background:{T['bg1']}; border:1px solid {T['violeta']}; }}"
            f"QLabel {{ color:{T['texto']}; background:transparent; }}"
            f"QProgressBar {{ background:{T['bg3']}; border:1px solid {T['linea']}; border-radius:8px; height:18px; text-align:center; color:{T['texto']}; }}"
            f"QProgressBar::chunk {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {T['cian']}, stop:1 {T['verde']}); border-radius:7px; }}"
            f"QPushButton {{ background:{T['bg3']}; color:{T['texto']}; border:1px solid {T['linea']}; border-radius:8px; padding:7px 16px; }}"
            f"QPushButton:hover {{ border:1px solid {T['rojo']}; }}")
        self._t0 = time.monotonic()
        self._total = max(1, total)
        lay = QVBoxLayout(self); lay.setContentsMargins(26, 20, 26, 18); lay.setSpacing(10)
        tit = QLabel("🔬 Analizando biblioteca"); tit.setStyleSheet(f"color:{T['violeta']}; font-size:16px; font-weight:bold;")
        lay.addWidget(tit)
        self.lbl = QLabel(); self.lbl.setStyleSheet("font-size:22px; font-weight:bold;")
        lay.addWidget(self.lbl)
        self.barra = QProgressBar(); self.barra.setRange(0, 1000); self.barra.setTextVisible(False); lay.addWidget(self.barra)
        self.lbl_vel = QLabel("Preparando…"); self.lbl_vel.setStyleSheet(f"color:{T['suave']};")
        lay.addWidget(self.lbl_vel)
        self.lbl_actual = EtiquetaElidida("")                  # una sola línea de alto fijo (recortada con "…"): si cambiara de alto, la ventana temblaría y cortaría los botones
        self.lbl_actual.setFixedHeight(18)
        self.lbl_actual.setStyleSheet(f"color:{T['cian']}; font-size:11px;")
        lay.addWidget(self.lbl_actual)
        info = f"Usando {procesos} procesos a máxima velocidad. " if procesos else "Máxima velocidad. "
        self._pie_extra = pie or "El reproductor queda oculto hasta que termine."
        pie = self.lbl_pie = QLabel(info + self._pie_extra); pie.setWordWrap(True)
        pie.setStyleSheet(f"color:{T['suave']}; font-size:11px;")
        lay.addWidget(pie)
        fila = QHBoxLayout(); fila.setSpacing(10); fila.addStretch(1)
        self.btn_fondo = QPushButton("⏬ Seguir en segundo plano")
        self.btn_fondo.setToolTip("Vuelve el reproductor y el análisis sigue en modo suave (más lento, sin cortar el audio)")
        self.btn_fondo.clicked.connect(self._fondo)
        self.btn_fondo.setVisible(permitir_fondo)
        fila.addWidget(self.btn_fondo)
        if volver is not None:                                # analizador aparte: desde acá se vuelve al reproductor (si no está abierto, se abre)
            self.btn_volver = QPushButton("🎧 Volver al reproductor")
            self.btn_volver.setToolTip("Trae el reproductor al frente (si está cerrado, lo abre). El análisis sigue acá.")
            self.btn_volver.clicked.connect(volver)
            fila.addWidget(self.btn_volver)
        self.btn = QPushButton("⏹ Detener")
        self.btn.setToolTip("Se detiene el análisis; lo ya analizado se conserva")
        self.btn.clicked.connect(self._cancelar)
        fila.addWidget(self.btn)
        lay.addLayout(fila)
        self.poner(0, total)
        try:                                                  # centrada en la pantalla (no depende de la ventana principal, que se oculta)
            g = QApplication.primaryScreen().availableGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)
        except Exception:
            pass

    def _cancelar(self):
        self.fase("Deteniendo…")
        self.cancelar.emit()

    def _fondo(self):
        self.fase("Pasando a segundo plano…")
        self.segundo_plano.emit()

    def poner(self, i, total):
        total = max(1, total); i = min(i, total)
        self._total = total
        gi, gt = self._base + i, self._base + total              # avance sobre el total de la lista, no solo sobre esta tanda
        pct = 100 * gi / gt
        self.lbl.setText(f"( {_fmt(gi)} / {_fmt(gt)} )   {pct:.0f} %")
        self.setWindowTitle(f"AutoMix 🎚️ — Analizando {pct:.0f} %")       # se ve en la barra de tareas aunque esté minimizada
        self.barra.setValue(int(1000 * gi / gt))
        dt = time.monotonic() - self._t0
        if i >= 3 and dt > 2:
            vel = i / dt
            self.lbl_vel.setText(f"≈ {vel:.1f} temas por segundo  ·  faltan unos {_tiempo((total - i) / max(vel, 1e-6))}")

    def reconfigurar(self, total, procesos=0, base=0):
        """La ventana se abre al instante (antes de saber cuántos temas hay): acá se le ponen los datos reales."""
        self._base = max(0, base)
        self._t0 = time.monotonic()
        self.lbl_pie.setText((f"Usando {procesos} procesos a máxima velocidad. " if procesos else "Máxima velocidad. ") + self._pie_extra)
        self.lbl_vel.setText("Preparando…")
        self.poner(0, total)

    def poner_actual(self, texto):
        """Qué temas se están analizando ahora (así se ve actividad aunque el número tarde en cambiar)."""
        self.lbl_actual.setText(texto)

    def fase(self, texto):
        self.lbl_vel.setText(texto)
        self.btn.setEnabled(False); self.btn_fondo.setEnabled(False)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:           # Esc no cierra: se cancela con el botón
            ev.ignore()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        ev.ignore() if self.btn.isEnabled() else ev.accept()
