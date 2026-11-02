"""AutoMix — ventana del historial de temas dañados."""
import os
import subprocess
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)
from setup.tema import TEMA


class DialogoDanados(QDialog):
    def __init__(self, parent, historial, al_cambiar):
        super().__init__(parent)
        self.historial = historial
        self.al_cambiar = al_cambiar                 # se llama cada vez que cambia la lista (para que la ventana actualice su contador y sus filtros)
        T = TEMA
        self.setWindowTitle("AutoMix 🎚️ — Temas dañados")
        self.resize(820, 420)
        self.setStyleSheet(                                      # mismo azul que los cuadros de mensaje (se distingue del reproductor)
            "QDialog { background:#1f3b63; border:2px solid #4fd1ff; } QLabel { color:#ffffff; background:transparent; }"
            "QTableWidget { background:#17304f; color:#ffffff; gridline-color:#35588a; border:1px solid #35588a; selection-background-color:#2f7fd0; selection-color:#ffffff; }"
            "QHeaderView::section { background:#2f5f99; color:#ffffff; border:0; border-right:1px solid #1f3b63; padding:5px; font-weight:bold; }"
            "QTableCornerButton::section { background:#2f5f99; border:0; }"
            "QPushButton { background:#2f5f99; color:#ffffff; border:1px solid #7fb4ee; border-radius:8px; padding:7px 14px; }"
            "QPushButton:hover { background:#3b74b8; border:1px solid #4fd1ff; }")
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(10)
        self.lbl = QLabel(); self.lbl.setStyleSheet("font-size:14px; font-weight:bold;")
        lay.addWidget(self.lbl)
        nota = QLabel("Archivos que no se pudieron cargar o analizar. La carga automática y el Random los saltean. "
                      "Si arreglás o reemplazás el archivo, se quita solo de la lista.")
        nota.setWordWrap(True); nota.setStyleSheet("color:#cfe3ff; font-size:11px;")
        lay.addWidget(nota)
        self.tabla = QTableWidget(0, 5)
        self.tabla.setHorizontalHeaderLabels(["Tema", "Carpeta", "Motivo", "Fecha", "Veces"])
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.verticalHeader().setVisible(False)
        h = self.tabla.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch); h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents); h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.tabla, 1)
        fila = QHBoxLayout(); fila.setSpacing(8)
        b1 = QPushButton("🔄 Volver a probar (quitar de la lista)"); b1.clicked.connect(self._quitar_sel)
        b2 = QPushButton("📂 Abrir carpeta"); b2.clicked.connect(self._abrir_carpeta)
        b3 = QPushButton("🗑 Vaciar historial"); b3.clicked.connect(self._vaciar)
        b4 = QPushButton("Cerrar"); b4.clicked.connect(self.accept)
        for b in (b1, b2, b3):
            fila.addWidget(b)
        fila.addStretch(1); fila.addWidget(b4)
        lay.addLayout(fila)
        self._llenar()

    def _llenar(self):
        items = self.historial.lista()
        self.tabla.setRowCount(len(items))
        for r, (k, v) in enumerate(items):
            vals = [v.get("nombre", ""), os.path.dirname(v.get("ruta", "")), v.get("motivo", ""), v.get("fecha", ""), str(v.get("veces", 1))]
            for c, t in enumerate(vals):
                it = QTableWidgetItem(t); it.setToolTip(t)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, k)
                self.tabla.setItem(r, c, it)
        self.lbl.setText(f"⚠ Temas dañados: {len(items)}" if items else "✅ No hay temas dañados registrados")

    def _claves_sel(self):
        filas = {i.row() for i in self.tabla.selectedIndexes()}
        return [self.tabla.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in sorted(filas)]

    def _quitar_sel(self):
        ks = self._claves_sel()
        if not ks:
            return
        self.historial.quitar(ks)
        self._llenar(); self.al_cambiar()

    def _vaciar(self):
        if not self.historial.datos:
            return
        r = QMessageBox.question(self, "AutoMix", "¿Vaciar todo el historial de temas dañados?\n(No borra ningún archivo.)")
        if r == QMessageBox.StandardButton.Yes:
            self.historial.vaciar()
            self._llenar(); self.al_cambiar()

    def _abrir_carpeta(self):
        ks = self._claves_sel()
        if not ks:
            return
        ruta = self.historial.datos.get(ks[0], {}).get("ruta", "")
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(ruta)])          # (explorer devuelve 1 aunque funcione: no se mira el código)
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(ruta)])
        except OSError:
            pass
