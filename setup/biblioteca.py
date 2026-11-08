"""AutoMix — lista de temas (biblioteca)."""
from pathlib import Path
import json
import bisect
import functools
import os
import re
import stat
import threading
import time
from PySide6.QtWidgets import QApplication, QAbstractItemView, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QStyledItemDelegate
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyleOptionViewItem
from PySide6.QtCore import QEventLoop, QMimeData, QTimer, QUrl, Qt, Signal
from setup.ajustes import FUENTE_BOOST, SUPPORTED
from setup.analisis import compat_armonica
from setup.base import CACHE_DIR, log
from setup import almacen, cruce_auto, indice
from setup.estadisticas import EST
from setup.tema import COLOR_DECK_A, COLOR_DECK_B, TEMA


# =====================================================================
# 3.7) BIBLIOTECA
# =====================================================================
BIBLIOTECA_JSON = CACHE_DIR / "biblioteca.json"


def _escanear(rutas):
    out = []
    for p in rutas:
        if os.path.isdir(p):
            for raiz, dirs, archivos in os.walk(p):
                dirs.sort(key=str.lower)
                for f in sorted(archivos, key=str.lower):
                    if f.lower().endswith(SUPPORTED):
                        out.append(os.path.join(raiz, f))
        elif os.path.isfile(p) and p.lower().endswith(SUPPORTED):
            out.append(p)
    return out


class _DelegadoFondo(QStyledItemDelegate):
    """Pinta el fondo (BackgroundRole) del item: la hoja de estilos lo ignora."""
    def paint(self, painter, option, index):
        br = index.data(Qt.ItemDataRole.BackgroundRole)
        if br is not None and br != QBrush():
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(br)
            painter.drawRoundedRect(option.rect.adjusted(1, 1, -1, -1), 5, 5)
            painter.restore()
        der = index.data(Qt.ItemDataRole.UserRole + 3)           # texto a la derecha (duración; en los títulos "cantidad / duración total")
        n_est = index.data(Qt.ItemDataRole.UserRole + 5) or 0    # estrellas (1 a 5, automáticas por reproducciones)
        hay_like = bool(index.data(Qt.ItemDataRole.UserRole + 6))  # 👍 marcado a mano
        if der or n_est or hay_like:
            opt = QStyleOptionViewItem(option)
            fm = QFontMetrics(opt.font)
            w_der = (fm.horizontalAdvance(der) + 12) if der else 0
            estrellas = "★" * int(n_est)
            w_est = (fm.horizontalAdvance(estrellas) + 8) if estrellas else 0
            w_like = (fm.horizontalAdvance("👍") + 8) if hay_like else 0
            opt.rect.setRight(opt.rect.right() - w_der - w_est - w_like)
            super().paint(painter, opt, index)
            painter.save()
            fg = index.data(Qt.ItemDataRole.ForegroundRole)
            painter.setPen(fg.color() if fg is not None and fg != QBrush() else option.palette.text().color())
            painter.setFont(opt.font)
            alin = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if der:
                painter.drawText(option.rect.adjusted(0, 0, -8, 0), alin, der)
            if estrellas:
                painter.setPen(QColor("#ffd400"))                                   # estrellas amarillas
                painter.drawText(option.rect.adjusted(0, 0, -(w_der + 8), 0), alin, estrellas)
            if hay_like:
                painter.drawText(option.rect.adjusted(0, 0, -(w_der + w_est + 8), 0), alin, "👍")
            painter.restore()
            return
        super().paint(painter, option, index)          # (antes decía `opt`, que solo existe si hay texto a la derecha: sin duración fallaba y la lista no se dibujaba)


def _reagrupa(f):
    """Los ordenamientos trabajan solo con temas: se sacan los encabezados de carpeta antes y se vuelven a poner después."""
    def w(self, *a, **k):
        if not self.agrupado:
            return f(self, *a, **k)
        sel = {self._clave(it.data(Qt.ItemDataRole.UserRole)) for it in self.selectedItems() if not self._es_cab(it)}
        barra = self.verticalScrollBar().value()
        self._quitar_cab()
        try:
            return f(self, *a, **k)
        finally:
            self._poner_cab()
            for i in range(self.count()):
                it = self.item(i)
                if not self._es_cab(it) and self._clave(it.data(Qt.ItemDataRole.UserRole)) in sel:
                    it.setSelected(True)
            self.verticalScrollBar().setValue(barra)
    w.__name__ = f.__name__
    return w


def _hay_musica():
    """True si algún deck está sonando (la ventana lo anota en hilos_analisis._SONANDO)."""
    try:
        from setup import hilos_analisis as _ha
        return bool(_ha._SONANDO)
    except Exception:
        return False


@functools.lru_cache(maxsize=100000)
def _clave_memo(p):
    """normcase(abspath) de una ruta, con memoria: en Windows es lento (Python puro) y con miles de temas se pedía miles de veces por pasada."""
    return os.path.normcase(os.path.abspath(p))


def _stem_memo(ruta):
    """Identidad del tema (nombre + tamaño): es el nombre de su análisis en el almacén y su clave en el índice.
    (El nombre de la función quedó de cuando la clave era solo el nombre del archivo.)"""
    return almacen.clave(ruta)


def _stem_viejo(ruta):
    """La clave de antes (solo el nombre del archivo): los análisis viejos todavía están en el índice con esa clave."""
    return Path(ruta).stem


@functools.lru_cache(maxsize=100000)
def _carpeta_memo(ruta):
    return os.path.normcase(os.path.dirname(os.path.abspath(ruta)))


class Biblioteca(QListWidget):
    aviso = Signal(str)        # mensaje para la barra de estado (p. ej. duplicados omitidos)
    cambio = Signal()
    cambio_estadisticas = Signal()        # se marcó o quitó un 👍 desde la lista
    estilo_listo = Signal(object)         # el hilo del orden por estilo terminó: (rutas en orden, cuántas con análisis) o el error
    estilo_progreso = Signal(int, int)    # (temas leídos, total) mientras el hilo lee los análisis
    nuevos = Signal()          # se agregaron temas: hay que analizarlos
    carga_progreso = Signal(int, int)     # al abrir: (temas revisados, total) mientras la lista se va llenando
    primera_tanda = Signal()              # ya hay temas en la lista (los decks guardados pueden volver a cargarse)
    lista_cargada = Signal()              # todos los temas ya están en la lista (todavía sin BPM / tono)
    carga_terminada = Signal()            # lista completa y con BPM / tonos leídos
    _precarga_lista = Signal()
    ROL_NOMBRE = Qt.ItemDataRole.UserRole + 1
    ROL_CAB = Qt.ItemDataRole.UserRole + 2         # True en los encabezados de carpeta
    ROL_CARPETA_ORIG = Qt.ItemDataRole.UserRole + 4  # ruta ORIGINAL de la carpeta (con mayúsculas reales): para arrastrar afuera

    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setUniformItemSizes(True)
        self.setItemDelegate(_DelegadoFondo(self))
        self.setAlternatingRowColors(False)                                    # todas las filas con el mismo fondo oscuro (#080b14)
        # ---- auto-ajuste al ancho de la lista (los ítems se estiran con el viewport, sin scroll horizontal) ----
        self.setResizeMode(QListView.ResizeMode.Adjust)                        # QListWidget hereda de QListView
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)                     # texto largo → "…" (el tooltip lo muestra completo)
        self.setWordWrap(False)
        # ----------------------------------------------------------------------------------------------------------
        f = self.font(); f.setPointSize(int(round(9 * FUENTE_BOOST))); self.setFont(f)
        self._rutas = set()
        self._cola = set()         # temas esperando su análisis (se muestran con ⏳)
        self._precargando = False  # True mientras el hilo de precarga lee los .json: la ventana NO los lee por su cuenta (con miles de temas congelaba todo)
        self._bombeo = False       # True durante los pasos finales del análisis masivo: los bucles largos atienden al sistema
        self._t_bomba = 0.0
        self._idx = None           # índice liviano (cache/indice.db) leído de una vez: {tema: (bpm, camelot, nombre, hay_clave, frase, voz, dur)}
        self._idx_ok = True        # False si el índice no se puede usar (se lee el .json de cada tema, como antes)
        self._idx_completo = False # True cuando todos los temas de la lista ya pasaron por el índice (sin índice = sin análisis, no hace falta mirar el disco)
        self._lote_idx = []        # filas leídas de un .json que todavía no estaban en el índice (se guardan de a muchas)
        self._marca_idx = 0        # hasta dónde se leyeron las novedades del índice
        self._memo = {}            # ruta -> (bpm, clave Camelot, nombre de la nota, hay_clave): se lee el .json una sola vez
        self._memo_frase = {}      # ruta -> True si el tema tiene grilla de compases firme (sirve para mezclar en frase)
        self._memo_dur = {}        # ruta -> duración en segundos (del análisis)
        self._memo_voz = {}        # ruta -> True si el análisis ya trae las curvas de voz/bajo
        self._skips = {}           # ruta (normalizada) -> True si está SALTEADO (no se carga solo al mezclar)
        self._tocados = set()      # temas tildados SOLOS por haber sonado más del 50 % (el botón «Destildar todos» saca solo estos)
        self._carga = None         # carga inicial en tandas (None = no hay ninguna en curso)
        self._cargando_lib = False # True mientras se carga la lista guardada: no se guarda (la lista está incompleta)
        self._precarga_lista.connect(self._fin_precarga)
        self._cargando = False     # guarda: bloquea reacciones a itemChanged mientras se cargan/ordenan cosas
        self.agrupado = False      # vista por carpetas: un encabezado por carpeta y debajo sus temas
        self._plegadas = set()     # carpetas cerradas (con clic en el encabezado)
        self.itemClicked.connect(self._clic_cab)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu_contextual)      # clic derecho: quitar tema / carpeta
        self.itemChanged.connect(self._on_item_changed)     # checkbox "saltear"
        self._referencia = None    # clave Camelot del tema que suena: los compatibles se pintan de verde

    # ---------- saltear temas ----------
    def esta_salteado(self, ruta):
        return self._skips.get(self._clave(ruta), False)

    def todas_activas(self):
        """Todas las rutas MENOS las que están tildadas como salteadas."""
        return [r for r in self.todas() if not self.esta_salteado(r)]

    def _on_item_changed(self, it):
        if self._cargando or self._es_cab(it):
            return
        ruta = it.data(Qt.ItemDataRole.UserRole)
        k = self._clave(ruta)
        skip = (it.checkState() == Qt.CheckState.Checked)   # ← TILDADO = salteado
        if self._skips.get(k, False) == skip:
            return                                   # nada cambió (por ejemplo, disparó por setText/setData)
        self._skips[k] = skip
        if not skip:
            self._tocados.discard(k)                 # lo destildaron a mano: ya no cuenta como "tocado"
        self._refrescar_textos()
        self.guardar()
        self.cambio.emit()

    def marcar_tocado(self, ruta):
        """El tema sonó más del 50 %: queda tildado (salteado) para que la carga automática no lo repita. Un tema que ya estaba
        tildado (a mano) se deja como está. Devuelve True si lo tildó."""
        k = self._clave(ruta)
        if self._skips.get(k, False):
            return False
        for i in range(self.count()):
            it = self.item(i)
            if not self._es_cab(it) and self._clave(it.data(Qt.ItemDataRole.UserRole)) == k:
                self._tocados.add(k)
                it.setCheckState(Qt.CheckState.Checked)      # dispara _on_item_changed: lo anota, actualiza el texto y guarda
                return True
        return False

    def destildar_tocados(self):
        """Destilda los temas que se tildaron solos por haber sonado. Los que se tildaron a mano no se tocan.
        Devuelve (cuántos destildó, cuántos tildados a mano quedan)."""
        if not self._tocados:
            return 0, sum(1 for v in self._skips.values() if v)
        prev = self._cargando
        self._cargando = True
        n = 0
        try:
            for i in range(self.count()):
                it = self.item(i)
                if self._es_cab(it):
                    continue
                k = self._clave(it.data(Qt.ItemDataRole.UserRole))
                if k in self._tocados:
                    it.setCheckState(Qt.CheckState.Unchecked)
                    self._skips[k] = False
                    n += 1
        finally:
            self._cargando = prev
        self._tocados.clear()
        self._refrescar_textos()
        self.guardar()
        self.cambio.emit()
        return n, sum(1 for v in self._skips.values() if v)

    def aplicar_orden(self, rutas):
        """Pone los temas en el orden de `rutas` (los que no estén en esa lista quedan al final, como estaban). Para la lista plana."""
        pos = {self._clave(r): i for i, r in enumerate(rutas)}
        n = self.count()
        sel = {self._clave(it.data(Qt.ItemDataRole.UserRole)) for it in self.selectedItems() if not self._es_cab(it)}
        items = [self.item(i) for i in range(n)]
        items = [it for it in items if not self._es_cab(it)]
        orden = sorted(items, key=lambda it: pos.get(self._clave(it.data(Qt.ItemDataRole.UserRole)), len(pos)))
        if [id(x) for x in orden] == [id(self.item(i)) for i in range(self.count())]:
            return
        prev = self._cargando
        self._cargando = True
        self.setUpdatesEnabled(False)
        try:
            self._sacar_todos()
            for it in orden:
                self._bomba()
                self.addItem(it)
                if self._clave(it.data(Qt.ItemDataRole.UserRole)) in sel:
                    it.setSelected(True)
        finally:
            self.setUpdatesEnabled(True)
            self._cargando = prev
        self.verticalScrollBar().setValue(0)
        if getattr(self, "_ult_pintado", None):
            self.pintar(*self._ult_pintado)
        self.cambio.emit()

    @staticmethod
    def _clave(p):
        return _clave_memo(p)

    @staticmethod
    def _firma(p, tam=None):
        """Identidad de un tema para detectar duplicados aunque estén en otra carpeta o se llamen "Tema (1)" / "Tema - copia":
        (nombre normalizado, tamaño del archivo). Dos archivos con el mismo nombre y el mismo tamaño son el mismo tema."""
        base = os.path.splitext(os.path.basename(p))[0].lower()
        nombre = re.sub(r"^\s*(copia|copy)\s+de\s+", "", base)                                   # "Copia de tema"
        for _ in range(3):                                                                       # "tema - copia (2)", "tema (1)"…
            nombre = re.sub(r"[\s_]*[\(\[]\s*\d+\s*[\)\]]\s*$", "", nombre)
            nombre = re.sub(r"[\s_\-]+[\(\[]?\s*(copia|copy|copie|kopie)\s*\d*\s*[\)\]]?\s*$", "", nombre)
        nombre = re.sub(r"[\s_]+", " ", nombre).strip() or base
        if tam is None:
            try:
                tam = os.path.getsize(p)
            except OSError:
                tam = -1
        return (nombre, tam)

    # ---------- vista por carpetas ----------
    def _es_cab(self, it):
        return bool(it.data(self.ROL_CAB))

    @staticmethod
    def _carpeta(ruta):
        return _carpeta_memo(ruta)

    def _sacar_todos(self):
        """Saca TODOS los temas de la lista y los devuelve en su orden. Se saca desde el final: sacar siempre el primero obliga a Qt a
        correr todos los demás (y a actualizar los temas ocultos) en cada vuelta, y con decenas de miles de temas tardaba minutos."""
        self.reset()                                  # (los temas ocultos hacen más lento cada takeItem: se muestran todos antes)
        items = []
        for i in range(self.count() - 1, -1, -1):
            self._bomba()
            items.append(self.takeItem(i))
        items.reverse()
        return items

    def _quitar_cab(self):
        self.reset()                              # de un golpe todos los temas vuelven a verse (uno por uno era lento con decenas de miles ocultos)
        for i in range(self.count() - 1, -1, -1):
            if self._es_cab(self.item(i)):
                self.takeItem(i)

    def _poner_cab(self):
        """Agrupa por carpeta (en orden alfabético); dentro de cada una los temas conservan el orden que tenían."""
        prev = self._cargando
        self._cargando = True
        try:
            items = self._sacar_todos()
            grupos = {}
            origs = {}                                    # ruta ORIGINAL (con mayúsculas reales) por carpeta
            for it in items:
                ruta = it.data(Qt.ItemDataRole.UserRole)
                k = self._carpeta(ruta)
                grupos.setdefault(k, []).append(it)
                if k not in origs:
                    origs[k] = os.path.dirname(os.path.abspath(ruta))
            for carpeta in sorted(grupos, key=lambda c: os.path.basename(c).lower()):
                cab = QListWidgetItem()
                cab.setData(self.ROL_CAB, True)
                cab.setData(Qt.ItemDataRole.UserRole, carpeta)
                cab.setData(self.ROL_CARPETA_ORIG, origs[carpeta])
                # Seleccionable + arrastrable: para poder arrastrar el título afuera y copiar toda la carpeta
                cab.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
                f = QFont(self.font()); f.setBold(True); cab.setFont(f)
                cab.setBackground(QBrush(QColor("#17566a"))); cab.setForeground(QBrush(QColor("#d8f6ff")))
                self.addItem(cab)
                cerrada = carpeta in self._plegadas
                nombre = os.path.basename(carpeta) or carpeta
                n_esp = sum(1 for it in grupos[carpeta] if self._clave(it.data(Qt.ItemDataRole.UserRole)) in self._cola)
                cab.setText(self._texto_cab(carpeta, cerrada, n_esp, len(grupos[carpeta])))
                self._estilo_cab(cab, bool(n_esp))
                cab.setToolTip(f"{origs[carpeta]}\n\nArrastrá este título para copiar TODA la carpeta afuera (a otra carpeta o al Escritorio).\nClic: abrir / cerrar.\nClic derecho (o Supr): quitar la carpeta de la lista (no borra archivos).")
                tot = sum((self.duracion(it.data(Qt.ItemDataRole.UserRole)) or 0) for it in grupos[carpeta])
                cab.setData(Qt.ItemDataRole.UserRole + 3, f"{len(grupos[carpeta])}" + (f" / {self._fmt_dur(tot, True)}" if tot else ""))
                for it in grupos[carpeta]:
                    self.addItem(it)
                    if cerrada:
                        self.setRowHidden(self.count() - 1, True)
        finally:
            self._cargando = prev

    def _agregar_cab(self, carpeta, orig, total):
        """Encabezado de carpeta al final de la lista (se usa mientras se carga la lista guardada: así las carpetas se ven desde el primer momento)."""
        cab = QListWidgetItem()
        cab.setData(self.ROL_CAB, True)
        cab.setData(Qt.ItemDataRole.UserRole, carpeta)
        cab.setData(self.ROL_CARPETA_ORIG, orig)
        cab.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
        f = QFont(self.font()); f.setBold(True); cab.setFont(f)
        cab.setBackground(QBrush(QColor("#17566a"))); cab.setForeground(QBrush(QColor("#d8f6ff")))
        self.addItem(cab)
        cab.setText(self._texto_cab(carpeta, carpeta in self._plegadas, 0, total))
        cab.setData(Qt.ItemDataRole.UserRole + 3, f"{total}")
        cab.setToolTip(f"{orig}\n\nArrastrá este título para copiar TODA la carpeta afuera (a otra carpeta o al Escritorio).\nClic: abrir / cerrar.\nClic derecho (o Supr): quitar la carpeta de la lista (no borra archivos).")

    @staticmethod
    def _texto_cab(carpeta, cerrada, n_esperando, total=0):
        """Título de la carpeta; si adentro hay temas esperando su análisis muestra ⏳ analizados / total (se ve aunque esté contraída)."""
        nombre = os.path.basename(carpeta) or carpeta
        reloj = ""
        if n_esperando:                                   # el reloj va entre la carpeta y el nombre: al final se cortaba con nombres largos
            reloj = (f"⏳ {max(0, total - n_esperando)}/{total}  " if total else "⏳ ")
        return ("▶ " if cerrada else "▼ ") + "📁 " + reloj + nombre

    @staticmethod
    def _estilo_cab(cab, pendiente):
        """Carpeta con temas sin analizar: se ve apagada (gris, como deshabilitada); completa: colores normales."""
        fondo, letra = ("#1b2a31", "#6f8790") if pendiente else ("#17566a", "#d8f6ff")
        if cab.background().color().name() != fondo:                      # solo se toca si cambió (evita repintar de más)
            cab.setBackground(QBrush(QColor(fondo))); cab.setForeground(QBrush(QColor(letra)))

    def _clic_cab(self, it):
        if not self._es_cab(it):
            return
        c = it.data(Qt.ItemDataRole.UserRole)
        if c in self._plegadas:
            self._plegadas.discard(c)
        else:
            self._plegadas.add(c)
        self._aplicar_plegado(c)                  # solo se muestran/ocultan los temas de ESA carpeta (antes se rearmaba toda la lista)

    def set_agrupado(self, v):
        v = bool(v)
        if v == self.agrupado:
            return
        if v:
            self.agrupado = True
            self._poner_cab()
        else:
            self._quitar_cab()
            self.agrupado = False
        self._refrescar_textos()
        if getattr(self, "_ult_pintado", None):
            self.pintar(*self._ult_pintado)

    # ---------- botón << (contraer / expandir todas las carpetas) ----------
    def plegar_todas(self):
        """Contrae TODAS las carpetas (vista por carpetas)."""
        if not self.agrupado:
            return
        for i in range(self.count()):
            it = self.item(i)
            if self._es_cab(it):
                self._plegadas.add(it.data(Qt.ItemDataRole.UserRole))
        self._aplicar_plegado()

    def desplegar_todas(self):
        """Expande TODAS las carpetas (vista por carpetas)."""
        if not self.agrupado:
            return
        self._plegadas.clear()
        self._aplicar_plegado()

    def _aplicar_plegado(self, solo=None):
        """Muestra u oculta los temas según las carpetas plegadas, SIN rearmar la lista (rearmarla con decenas de miles de temas
        tardaba minutos). `solo`: la carpeta a actualizar (None = todas). También actualiza el título de cada carpeta."""
        prev = self._cargando
        self._cargando = True
        self.setUpdatesEnabled(False)
        try:
            cab = None; cerrada = False; actualizar = False; n_tot = n_esp = 0
            def cerrar_cab():
                if cab is not None and actualizar:
                    c = cab.data(Qt.ItemDataRole.UserRole)
                    nuevo = self._texto_cab(c, cerrada, n_esp, n_tot)
                    if cab.text() != nuevo:
                        cab.setText(nuevo)
                    self._estilo_cab(cab, bool(n_esp))
            for i in range(self.count()):
                self._bomba()
                it = self.item(i)
                if self._es_cab(it):
                    cerrar_cab()
                    cab = it
                    c = it.data(Qt.ItemDataRole.UserRole)
                    cerrada = c in self._plegadas
                    actualizar = solo is None or c == solo
                    n_tot = n_esp = 0
                    continue
                if not actualizar:
                    continue
                n_tot += 1
                if self._clave(it.data(Qt.ItemDataRole.UserRole)) in self._cola:
                    n_esp += 1
                if it.isHidden() != cerrada:
                    it.setHidden(cerrada)
            cerrar_cab()
        finally:
            self.setUpdatesEnabled(True)
            self._cargando = prev

    def todas_plegadas(self):
        """True si TODAS las carpetas están contraídas (o no hay ninguna carpeta)."""
        if not self.agrupado:
            return False
        carpetas = set()
        for i in range(self.count()):
            it = self.item(i)
            if self._es_cab(it):
                carpetas.add(it.data(Qt.ItemDataRole.UserRole))
        return bool(carpetas) and carpetas.issubset(self._plegadas)

    @staticmethod
    def _fmt_dur(seg, largo=False):
        seg = int(round(seg)); h, r = divmod(seg, 3600); m, sg = divmod(r, 60)
        return f"{h}:{m:02d}:{sg:02d}" if (h or largo) else f"{m}:{sg:02d}"

    def duracion(self, ruta):
        if not self._precargando:                         # durante la precarga se usa lo ya leído (al terminar, los títulos de carpeta se recalculan)
            self.info(ruta)
        return self._memo_dur.get(self._clave(ruta))

    def _nombre(self, it):
        n = it.data(self.ROL_NOMBRE)
        return n if n is not None else it.text()

    def _bomba(self):
        """Con miles de temas, los recorridos de la lista duran segundos: si el hilo de la ventana no atiende al sistema,
        Windows la marca "No responde". Con _bombeo activo, cada ~0,1 s se atienden los mensajes (sin clics ni teclas)."""
        if not self._bombeo:
            return
        ahora = time.monotonic()
        if ahora - self._t_bomba > 0.1:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            self._t_bomba = time.monotonic()

    def _refrescar_textos(self):
        prev = self._cargando
        self._cargando = True
        try:
            pend = {}                                     # temas esperando su análisis, por carpeta (para el título)
            tot_c = {}                                    # total de temas por carpeta
            for i in range(self.count()):
                self._bomba()
                it = self.item(i)
                if self._es_cab(it):
                    continue
                esperando = self._clave(it.data(Qt.ItemDataRole.UserRole)) in self._cola
                k_c = self._carpeta(it.data(Qt.ItemDataRole.UserRole))
                tot_c[k_c] = tot_c.get(k_c, 0) + 1
                if esperando:
                    pend[k_c] = pend.get(k_c, 0) + 1
                bpm, clave = self.info(it.data(Qt.ItemDataRole.UserRole))[:2]
                pref = (f"{bpm:.0f} - " if bpm else "") + (f"[{clave}] " if clave else "")      # ej.: "124 - [8A] nombre del tema"
                nom = self._nombre(it)
                if bpm:                                   # el BPM ya va adelante: se saca el "[73 BPM]" que traiga el nombre del archivo
                    nom = re.sub(r"\s*[\[\(]\s*\d+(?:[.,]\d+)?\s*BPM\s*[\]\)]\s*", " ", nom, flags=re.I).strip() or nom
                salteado = self.esta_salteado(it.data(Qt.ItemDataRole.UserRole))
                prefijo = "⏭ " if salteado else ""
                nuevo = prefijo + ("⏳ " if esperando else "") + pref + nom
                if it.text() != nuevo:
                    it.setText(nuevo)
                d = self.duracion(it.data(Qt.ItemDataRole.UserRole))
                it.setData(Qt.ItemDataRole.UserRole + 3, self._fmt_dur(d) if d else None)
                self._poner_est(it, it.data(Qt.ItemDataRole.UserRole))
            for i in range(self.count()):                 # títulos de carpeta: ⏳ n si hay temas pendientes adentro
                it = self.item(i)
                if self._es_cab(it):
                    c = it.data(Qt.ItemDataRole.UserRole)
                    nuevo = self._texto_cab(c, c in self._plegadas, pend.get(c, 0), tot_c.get(c, 0))
                    if it.text() != nuevo:
                        it.setText(nuevo)
                    self._estilo_cab(it, bool(pend.get(c, 0)))
        finally:
            self._cargando = prev

    def marcar(self, rutas):
        """Los temas de esta lista aparecen con el reloj de arena hasta que se los analiza."""
        self._cola = {self._clave(r) for r in rutas}
        self._refrescar_textos()

    def quitar_de_cola(self, ruta):
        k = self._clave(ruta)
        self._memo.pop(k, None); self._memo_frase.pop(k, None); self._memo_voz.pop(k, None)     # terminó su análisis: se vuelve a leer
        self._cola.discard(k)
        self._refrescar_idx([ruta])
        self._refrescar_textos()
        self.pintar(*self._ult_pintado) if getattr(self, "_ult_pintado", None) else None

    def quitar_de_cola_lote(self, rutas):
        """Igual que quitar_de_cola pero para varios temas: la lista se recorre y se repinta UNA sola vez
        (con miles de temas, hacerlo por cada tema analizado dejaba la ventana trabada)."""
        for ruta in rutas:
            self._bomba()
            k = self._clave(ruta)
            self._memo.pop(k, None); self._memo_frase.pop(k, None); self._memo_voz.pop(k, None)
            self._cola.discard(k)
        self._refrescar_idx(rutas)
        self._refrescar_textos()
        self.pintar(*self._ult_pintado) if getattr(self, "_ult_pintado", None) else None

    def _refrescar_idx(self, rutas):
        """Los temas recién analizados en este programa: se vuelve a leer su fila del índice (de una consulta por tandas)."""
        if self._idx is None or not self._idx_ok:
            return
        por_st = {_stem_memo(r): r for r in rutas}
        stems = set(por_st)
        try:
            self._idx.update(indice.por_temas(stems))
        except Exception as e:
            log("índice", f"no se pudo actualizar desde el índice: {e}")
        for st, r in por_st.items():                         # recién analizado pero sin fila en la base (base ilegible, pendrive…): se lee su análisis
            if st in self._idx and self._idx[st][0] is not None and self._idx[st][3]:
                continue
            try:
                hallado = almacen.buscar(r)
                if hallado is not None:
                    d = almacen.leer(hallado)
                    if isinstance(d, dict) and "bpm" in d:
                        self._lote_idx.append(indice.fila_de(st, d))
            except Exception:
                pass
        self._guardar_lote_idx()

    def info(self, ruta):
        """(bpm, clave Camelot, nombre de la nota, hay_clave) del tema, desde su caché de análisis."""
        k = self._clave(ruta)
        v = self._memo.get(k)
        if v is None:
            if self._idx is None and self._idx_ok:
                self._cargar_idx()
            if self._idx_ok:
                fila = self._idx.get(_stem_memo(ruta))
                if fila is None:
                    fila = self._idx.get(_stem_viejo(ruta))   # análisis viejo (todavía con la clave de solo el nombre)
                if fila is not None:                          # lo normal: viene del índice, sin tocar el disco
                    v = self._poner_memo(k, fila)
                    return v
                if self._idx_completo:                        # ya se revisó todo: lo que no está en el índice no tiene análisis
                    v = (None, None, None, False)
                    self._memo[k] = v; self._memo_frase[k] = False; self._memo_voz[k] = False
                    return v
            bpm = cam = nom = None; hay = False; fr = False; hv = False
            try:
                cache = almacen.buscar(ruta)
                if cache is not None:
                    d = almacen.leer(cache)
                    b = d.get("bpm"); bpm = float(b) if b is not None else None
                    fr = len(d.get("downbeats_full") or d.get("downbeats") or []) >= 9 and d.get("grid") != 0
                    hv = "voz" in d
                    try:
                        self._memo_dur[k] = float(d.get("duration") or 0) or None
                    except Exception:
                        pass
                    if "key" in d:
                        hay = True
                        cam = (d["key"] or {}).get("camelot"); nom = (d["key"] or {}).get("nombre")
                    if self._idx_ok and isinstance(d, dict) and "bpm" in d:        # todavía no estaba en el índice: se anota (migración automática)
                        self._lote_idx.append(indice.fila_de(_stem_memo(ruta), d))
            except Exception:
                pass
            v = (bpm, cam, nom, hay)
            self._memo[k] = v
            self._memo_frase[k] = bool(fr)
            self._memo_voz[k] = bool(hv)
        return v

    def _poner_memo(self, k, fila):
        """Carga en la memoria de la lista una fila del índice: (bpm, camelot, nombre, hay_clave, frase, voz, dur)."""
        bpm, cam, nom, hay, fr, voz, dur = fila
        v = (bpm, cam, nom, bool(hay))
        self._memo[k] = v
        self._memo_frase[k] = bool(fr)
        self._memo_voz[k] = bool(voz)
        if dur:
            self._memo_dur[k] = dur
        return v

    def _cargar_idx(self):
        """Lee todo el índice de una sola consulta (decenas de miles de temas en una fracción de segundo)."""
        try:
            self._idx = indice.todo()
            self._marca_idx = max(self._marca_idx, indice.marca_actual())
        except Exception as e:
            log("índice", f"no se pudo leer el índice ({e}): se leen los .json uno por uno")
            self._idx = {}
            self._idx_ok = False

    def _guardar_lote_idx(self):
        """Guarda en el índice lo leído de los .json (de a muchos, en una sola transacción)."""
        lote, self._lote_idx = self._lote_idx, []
        if not lote:
            return
        for f in lote:                                       # la lista ya los usa, se pueda guardar en la base o no
            self._idx[f[0]] = f[1:8]
        try:
            indice.registrar_lote(lote)
        except Exception as e:
            log("índice", f"no se pudo guardar en el índice: {e}")

    def sincronizar_indice(self, max_filas=5000):
        """Mira qué temas se analizaron desde la última vez (los analiza otro programa) y actualiza SOLO esas filas de la lista.
        Devuelve cuántos temas nuevos había."""
        if self._idx is None:
            self._cargar_idx()
        if not self._idx_ok:
            return 0
        filas, self._marca_idx = indice.novedades(self._marca_idx, max_filas)
        if not filas:
            return 0
        cambiados = set()
        for f in filas:
            self._idx[f[0]] = f[1:]
            cambiados.add(f[0])
        prev = self._cargando
        self._cargando = True
        try:
            for i in range(self.count()):
                it = self.item(i)
                if self._es_cab(it):
                    continue
                ruta = it.data(Qt.ItemDataRole.UserRole)
                if _stem_memo(ruta) not in cambiados:
                    continue
                k = self._clave(ruta)
                self._memo.pop(k, None); self._memo_frase.pop(k, None); self._memo_voz.pop(k, None)
                self._cola.discard(k)
                self._texto_item(it, ruta)
        finally:
            self._cargando = prev
        return len(filas)

    def _texto_item(self, it, ruta):
        """Texto de un tema en la lista: '⏭ ⏳ 124 - [8A] nombre' (igual que _refrescar_textos, pero de un solo tema)."""
        bpm, clave = self.info(ruta)[:2]
        pref = (f"{bpm:.0f} - " if bpm else "") + (f"[{clave}] " if clave else "")
        nom = self._nombre(it)
        if bpm:
            nom = re.sub(r"\s*[\[\(]\s*\d+(?:[.,]\d+)?\s*BPM\s*[\]\)]\s*", " ", nom, flags=re.I).strip() or nom
        esperando = self._clave(ruta) in self._cola
        nuevo = ("⏭ " if self.esta_salteado(ruta) else "") + ("⏳ " if esperando else "") + pref + nom
        if it.text() != nuevo:
            it.setText(nuevo)
        d = self.duracion(ruta)
        it.setData(Qt.ItemDataRole.UserRole + 3, self._fmt_dur(d) if d else None)
        self._poner_est(it, ruta)

    def _poner_est(self, it, ruta):
        """Estrellas (automáticas) y 👍 (manual) del tema, para que el delegado las dibuje a la derecha."""
        est, like = (EST.estrellas(ruta) if cruce_auto.EST_ESTRELLAS >= 0.5 else 0), EST.like(ruta)       # estrellas ocultas por defecto (se muestran desde ⚙ Ajustes)
        if (it.data(Qt.ItemDataRole.UserRole + 5) or 0) != est:
            it.setData(Qt.ItemDataRole.UserRole + 5, est)
        if bool(it.data(Qt.ItemDataRole.UserRole + 6)) != like:
            it.setData(Qt.ItemDataRole.UserRole + 6, like)

    def refrescar_estadisticas(self):
        """Vuelve a poner las estrellas y los 👍 en toda la lista (después de contar una reproducción o marcar un 👍)."""
        prev = self._cargando
        self._cargando = True
        try:
            for i in range(self.count()):
                it = self.item(i)
                if not self._es_cab(it):
                    self._poner_est(it, it.data(Qt.ItemDataRole.UserRole))
        finally:
            self._cargando = prev
        self.viewport().update()

    def frase_ok(self, ruta):
        self.info(ruta)
        return self._memo_frase.get(self._clave(ruta), False)

    @_reagrupa
    def ordenar_inteligente(self, armonico, frase, inicio=None):
        """Ordena la lista de arriba hacia abajo como una cadena de mezcla: cada tema es el que mejor engancha con el
        anterior (clave compatible primero, BPM parecido después). Con 'frase' van primero los que tienen grilla de
        compases firme. Los que todavía no tienen análisis quedan al final. `inicio`: tema donde arranca la cadena."""
        n = self.count()
        if n < 2:
            return
        barra = self.verticalScrollBar().value()
        sel = {self._clave(it.data(Qt.ItemDataRole.UserRole)) for it in self.selectedItems() if not self._es_cab(it)}
        items = [self.item(i) for i in range(n)]
        datos = []
        for it in items:
            self._bomba()
            r = it.data(Qt.ItemDataRole.UserRole)
            bpm, cam, _, hay = self.info(r)
            datos.append({"it": it, "r": r, "k": self._clave(r), "bpm": bpm, "cam": cam if armonico else None,
                          "ok": (not frase) or self.frase_ok(r), "nombre": self._nombre(it).lower()})
        listos, resto = [], []
        for d in datos:                                       # (una sola pasada: con miles de temas, "d not in listos" era cuadrático)
            (listos if (d["bpm"] and (d["ok"] or not frase) and (d["cam"] or not armonico)) else resto).append(d)
        listos.sort(key=lambda d: (d["bpm"], d["nombre"]))
        cadena = []
        if listos:
            ini = next((d for d in listos if inicio and d["k"] == self._clave(inicio)), listos[0])
            cadena = self._encadenar(listos, ini, armonico, self._bomba)
        resto.sort(key=lambda d: (d["bpm"] is None, d["bpm"] or 0.0, d["nombre"]))     # sin clave/grilla: al final, por BPM
        orden = [d["k"] for d in cadena + resto]
        if orden == [self._clave(self.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(n)]:
            return                                              # ya está en ese orden: no se toca nada
        prev = self._cargando
        self._cargando = True
        self.setUpdatesEnabled(False)
        try:
            self._sacar_todos()
            for d in cadena + resto:
                self._bomba()
                self.addItem(d["it"])
                if d["k"] in sel:
                    d["it"].setSelected(True)
        finally:
            self.setUpdatesEnabled(True)
            self._cargando = prev
        self.verticalScrollBar().setValue(barra)
        if getattr(self, "_ult_pintado", None):
            self.pintar(*self._ult_pintado)
        self.cambio.emit()

    # con más temas que esto la cadena se arma mirando solo los vecinos más cercanos en BPM (mirar TODOS en cada paso es cuadrático:
    # con 34.000 temas tardaba minutos y la ventana quedaba "No responde")
    _CADENA_EXACTA_HASTA = 1500
    _CADENA_VENTANA = 96

    @staticmethod
    def _encadenar(listos, ini, armonico, bomba=None):
        """Arma la cadena de mezcla: empieza en `ini` y en cada paso toma el tema que mejor engancha con el anterior.
        `listos` viene ordenada por BPM. Hasta _CADENA_EXACTA_HASTA temas mira todos los que quedan (resultado exacto);
        con más, solo los _CADENA_VENTANA vecinos de cada lado por BPM (casi igual y miles de veces más rápido)."""
        def puntaje_de(cur):
            def puntaje(d):
                nivel = compat_armonica(cur["cam"], d["cam"]) if armonico else 0
                dif = abs(d["bpm"] / cur["bpm"] - 1.0)
                return ((nivel or 0) * 10.0 - dif * 150.0, -d["bpm"])
            return puntaje
        listos = list(listos)
        cur = ini
        listos.remove(ini)
        cadena = [cur]
        if len(listos) + 1 <= Biblioteca._CADENA_EXACTA_HASTA:
            while listos:
                if bomba:
                    bomba()
                cur = max(listos, key=puntaje_de(cur))
                listos.remove(cur); cadena.append(cur)
            return cadena
        bpms = [d["bpm"] for d in listos]                      # paralela a `listos` (ambas ordenadas por BPM)
        v = Biblioteca._CADENA_VENTANA
        while listos:
            if bomba:
                bomba()
            p = bisect.bisect_left(bpms, cur["bpm"])
            lo, hi = max(0, p - v), min(len(listos), p + v)
            if lo >= hi:
                lo, hi = max(0, len(listos) - 1), len(listos)
            pf = puntaje_de(cur)
            j = max(range(lo, hi), key=lambda i: pf(listos[i]))
            if armonico and pf(listos[j])[0] < 0.0 and (hi - lo) < len(listos):      # nada compatible en la ventana: se mira más lejos (pasa casi solo al final)
                lo2, hi2 = max(0, p - 8 * v), min(len(listos), p + 8 * v)
                j = max(range(lo2, hi2), key=lambda i: pf(listos[i]))
            cur = listos.pop(j); bpms.pop(j)
            cadena.append(cur)
        return cadena

    def set_referencia(self, clave):
        if clave != self._referencia:
            self._referencia = clave
            if getattr(self, "_ult_pintado", None):
                self.pintar(*self._ult_pintado)

    def _firmas_actuales(self):
        return {self._firma(r) for r in self.todas()}

    def _crear_item(self, p, clave):
        nombre = os.path.splitext(os.path.basename(p))[0]
        it = QListWidgetItem(nombre)
        it.setData(self.ROL_NOMBRE, nombre)
        it.setData(Qt.ItemDataRole.UserRole, p)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        # ← casilla TILDADA solo si venía guardado como salteado; si no, arranca DESTILDADA
        it.setCheckState(Qt.CheckState.Checked if self._skips.get(clave) else Qt.CheckState.Unchecked)
        it.setToolTip(p + "\n\nCasilla tildada = SALTEAR: la carga automática no lo pone en el reproductor.")
        return it

    def agregar(self, rutas, permitir_repetidos=False):
        n = 0
        dup = 0
        ya = 0                                         # temas cuyo mismo archivo ya estaba en la lista
        repetidos = []                                 # rutas descartadas por ser copia de un tema que ya está
        firmas = self._firmas_actuales()               # lo que ya está en la lista (por nombre + tamaño)
        prev = self._cargando
        self._cargando = True
        self.setUpdatesEnabled(False)
        try:
            for p in _escanear(rutas):
                clave = os.path.normcase(os.path.abspath(p))
                if clave in self._rutas:
                    ya += 1
                    continue                           # el mismo archivo, ya está
                f = self._firma(p)
                if f in firmas and not permitir_repetidos:
                    dup += 1                           # una copia de un tema que ya está (otra carpeta, "(1)", "- copia"…)
                    repetidos.append(p)
                    log("biblioteca", f"duplicado omitido: {os.path.basename(p)}")
                    continue
                firmas.add(f)
                self._rutas.add(clave)
                self.addItem(self._crear_item(p, clave))
                n += 1
        finally:
            self._cargando = prev
            self.setUpdatesEnabled(True)
        if n and self.agrupado:
            self._quitar_cab(); self._poner_cab()
        if n:
            log("biblioteca", f"+{n} tema(s) agregado(s) · total {self.count()}")
            self._refrescar_textos()
            self.cambio.emit()
            self.nuevos.emit()
        if dup:
            self.aviso.emit(f"♻️ {dup} tema(s) repetido(s) no se agregaron" + (f" · {n} nuevo(s)" if n else ""))
        self.ultimo_resumen = {"nuevos": n, "ya_estaban": ya, "repetidos": repetidos}
        return n

    def quitar_seleccion(self):
        """Quita de la lista lo seleccionado. Si se selecciona un TÍTULO de carpeta se quita la carpeta entera (con sus temas),
        previa confirmación. Nunca se borra ningún archivo del disco."""
        sel = self.selectedItems()
        carpetas = {it.data(Qt.ItemDataRole.UserRole) for it in sel if self._es_cab(it)}
        items = {id(it): it for it in sel if not self._es_cab(it)}
        if carpetas:
            for i in range(self.count()):
                it = self.item(i)
                if not self._es_cab(it) and self._carpeta(it.data(Qt.ItemDataRole.UserRole)) in carpetas:
                    items[id(it)] = it
            nombres = ", ".join("«" + (os.path.basename(c) or c) + "»" for c in sorted(carpetas, key=str.lower))
            r = QMessageBox.question(self, "Quitar carpeta de la lista",
                                     f"¿Quitar de la lista {'la carpeta' if len(carpetas) == 1 else 'las carpetas'} {nombres} con sus {len(items)} tema(s)?\n\n"
                                     "No se borra ningún archivo del disco: solo salen de la lista de AutoMix.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        items = list(items.values())
        siguiente = None if carpetas else self._tema_vecino(items)      # para seguir con Supr apretado: queda seleccionado el tema que seguía
        log("biblioteca", f"quitando {len(items)} tema(s)" + (f" · {len(carpetas)} carpeta(s)" if carpetas else ""))
        self._cargando = True
        try:
            for it in items:
                p = it.data(Qt.ItemDataRole.UserRole)
                self._rutas.discard(os.path.normcase(os.path.abspath(p)))
                self.takeItem(self.row(it))
        finally:
            self._cargando = False
        if self.agrupado:
            self._quitar_cab(); self._poner_cab()
        if siguiente:
            self._seleccionar_ruta(siguiente)
        self.cambio.emit()

    def _tema_vecino(self, quitados):
        """Ruta del tema (no título de carpeta) que sigue al último de los quitados; si no hay, el que está justo antes."""
        ids = {id(it) for it in quitados}
        filas = [self.row(it) for it in quitados]
        if not filas:
            return None
        despues = [self.item(i) for i in range(max(filas) + 1, self.count())]
        antes = [self.item(i) for i in range(min(filas) - 1, -1, -1)]
        for it in despues + antes:
            if id(it) not in ids and not self._es_cab(it):
                return it.data(Qt.ItemDataRole.UserRole)
        return None

    def _seleccionar_ruta(self, ruta):
        """Deja seleccionado (y como actual) el tema de esa ruta, para que Supr siga quitando el que viene."""
        for i in range(self.count()):
            it = self.item(i)
            if not self._es_cab(it) and it.data(Qt.ItemDataRole.UserRole) == ruta:
                self.clearSelection()
                self.setCurrentItem(it)
                it.setSelected(True)
                self.scrollToItem(it)
                return

    def _menu_contextual(self, pos):
        """Clic derecho: quitar de la lista el tema (o toda la carpeta, si se toca su título)."""
        it = self.itemAt(pos)
        if it is None:
            return
        if not it.isSelected():
            self.clearSelection(); it.setSelected(True)
        menu = QMenu(self)
        if self._es_cab(it):
            c = it.data(Qt.ItemDataRole.UserRole)
            n = sum(1 for i in range(self.count()) if not self._es_cab(self.item(i)) and self._carpeta(self.item(i).data(Qt.ItemDataRole.UserRole)) == c)
            acc = menu.addAction(f"🗑 Quitar la carpeta de la lista ({n} temas)")
        else:
            sel = [x.data(Qt.ItemDataRole.UserRole) for x in self.selectedItems() if not self._es_cab(x)]
            n = len(sel)
            todos_like = bool(sel) and all(EST.like(r) for r in sel)
            acc_like = menu.addAction(("👍 Quitar el 👍" if todos_like else "👍 Me gusta (marcar)") + ("" if n <= 1 else f" ({n} temas)"))
            acc = menu.addAction("🗑 Quitar de la lista" if n <= 1 else f"🗑 Quitar de la lista ({n} temas)")
        elegido = menu.exec(self.viewport().mapToGlobal(pos))
        if elegido is None:
            return
        if not self._es_cab(it) and elegido is acc_like:
            EST.poner_like(sel, not todos_like)
            self.refrescar_estadisticas()
            self.cambio_estadisticas.emit()
        elif elegido is acc:
            self.quitar_seleccion()

    def limpiar(self):
        log("biblioteca", "lista vaciada")
        self._carga = None; self._cargando_lib = False          # (si todavía se estaba cargando al abrir, se cancela)
        bs = self.blockSignals(True)                 # con miles de temas, cada fila borrada disparaba señales y repintados (30+ s)
        self.setUpdatesEnabled(False)
        try:
            self.clear()
        finally:
            self.blockSignals(bs)
            self.setUpdatesEnabled(True)
        self._rutas.clear(); self._cola.clear(); self.cambio.emit()

    def pintar(self, ruta_a, ruta_b):
        self._ult_pintado = (ruta_a, ruta_b)
        ka = os.path.normcase(os.path.abspath(ruta_a)) if ruta_a else None
        kb = os.path.normcase(os.path.abspath(ruta_b)) if ruta_b else None
        for i in range(self.count()):
            self._bomba()
            it = self.item(i)
            if self._es_cab(it):
                continue
            ruta = it.data(Qt.ItemDataRole.UserRole)
            k = os.path.normcase(os.path.abspath(ruta))
            if k == ka:
                it.setBackground(QBrush(QColor(COLOR_DECK_A))); it.setForeground(QBrush(QColor(TEMA["bg0"])))
            elif k == kb:
                it.setBackground(QBrush(QColor(COLOR_DECK_B))); it.setForeground(QBrush(QColor(TEMA["bg0"])))
            else:
                nivel = compat_armonica(self._referencia, self.info(ruta)[1]) if (self._referencia and not self.agrupado) else None     # por carpetas: sin colores de compatibilidad
                if nivel == 2:                      # misma clave o relativa
                    it.setBackground(QBrush(QColor("#0d4a30"))); it.setForeground(QBrush(QColor("#7dffbf")))
                elif nivel == 1:                    # vecina en la rueda
                    it.setBackground(QBrush(QColor("#3b4410"))); it.setForeground(QBrush(QColor("#e1ff8a")))
                else:
                    it.setData(Qt.ItemDataRole.BackgroundRole, None)
                    it.setData(Qt.ItemDataRole.ForegroundRole, None)

    def todas(self):
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count()) if not self._es_cab(self.item(i))]

    def todas_visibles(self):
        """Todas las rutas VISIBLES ahora mismo en la lista: excluye títulos de carpeta y temas ocultos
        (por el filtro de búsqueda o por una carpeta cerrada). Es la 'cola' real de lo que el usuario ve.
        Sirve para que la carga automática (doble clic, mezcla automática, botón F2) respete el filtro."""
        return [self.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.count())
                if not self._es_cab(self.item(i)) and not self.item(i).isHidden()]

    def _bpm_de(self, ruta):
        return self.info(ruta)[0]

    def temas_sin_bpm(self):
        return [r for r in self.todas() if self._bpm_de(r) is None]

    def contar_analizados(self):
        """Cantidad de temas de la lista ya analizados por completo (BPM, tonalidad y voz), contados desde el índice en memoria."""
        if self._idx is None or not self._idx_ok:
            return None
        idx = self._idx
        n = 0
        for r in self.todas():
            fila = idx.get(_stem_memo(r)) or idx.get(_stem_viejo(r))
            if fila is not None and fila[0] is not None and fila[3] and fila[5]:
                n += 1
        return n

    def temas_sin_analisis(self):
        """Sin BPM, o con análisis viejo al que le falta la tonalidad."""
        out = []
        for r in self.todas():
            bpm, _, _, hay_clave = self.info(r)
            if bpm is None or not hay_clave or not self._memo_voz.get(self._clave(r), False):
                out.append(r)
        return out

    @_reagrupa
    def ordenar_por_bpm(self):
        prev = self._cargando
        self._cargando = True
        self.setUpdatesEnabled(False)
        try:
            items = []
            for it in self._sacar_todos():
                self._bomba()
                ruta = it.data(Qt.ItemDataRole.UserRole)
                bpm = self._bpm_de(ruta)
                items.append((bpm, it))
            items.sort(key=lambda x: (x[0] is None,
                                      x[0] if x[0] is not None else 0.0,
                                      self._nombre(x[1]).lower()))
            for _, it in items:
                self._bomba()
                self.addItem(it)
        finally:
            self.setUpdatesEnabled(True)
            self._cargando = prev
        self._refrescar_textos()
        self.cambio.emit()

    def guardar(self):
        if self._cargando_lib:                      # la lista todavía se está cargando: guardar ahora la dejaría incompleta
            return
        try:
            BIBLIOTECA_JSON.parent.mkdir(exist_ok=True)
            datos = [{"ruta": r, "skip": self.esta_salteado(r), **({"toc": True} if self._clave(r) in self._tocados else {})} for r in self.todas()]
            BIBLIOTECA_JSON.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print("No se pudo guardar la biblioteca:", e)

    def cargar(self):
        """Lee la lista guardada y la carga EN TANDAS: la ventana se abre enseguida y la lista se va llenando
        (con miles de temas, revisar cada archivo y leer su análisis tardaba varios segundos con la ventana congelada)."""
        try:
            datos = json.loads(BIBLIOTECA_JSON.read_text(encoding="utf-8"))
        except Exception:
            return
        rutas = []
        self._skips = {}
        self._tocados = set()
        for d in datos:
            if isinstance(d, str):                       # formato viejo: solo la ruta
                rutas.append(d)
            elif isinstance(d, dict) and d.get("ruta"):
                rutas.append(d["ruta"])
                if d.get("skip"):
                    self._skips[os.path.normcase(os.path.abspath(d["ruta"]))] = True
                    if d.get("toc"):
                        self._tocados.add(os.path.normcase(os.path.abspath(d["ruta"])))
        if not rutas:
            return
        self._carga = {"rutas": rutas, "i": 0, "n": 0, "dup": 0, "firmas": self._firmas_actuales(), "primera": False}
        self._cargando_lib = True
        QTimer.singleShot(0, self._cargar_tanda)

    def _cargar_tanda(self):
        c = self._carga
        if c is None:
            return
        t0 = time.perf_counter()
        prev = self._cargando
        self._cargando = True
        self.setUpdatesEnabled(False)
        try:
            rutas = c["rutas"]
            if "agr" not in c:                                                    # primera tanda: si la vista es por carpetas, la lista se arma YA con sus carpetas (contraídas)
                c["agr"] = bool(self.agrupado)
                c["ult"] = None
                c["tot"] = {}
                if c["agr"]:
                    for r in rutas:
                        k = self._carpeta(r)
                        c["tot"][k] = c["tot"].get(k, 0) + 1
                    rutas.sort(key=lambda r: (os.path.basename(self._carpeta(r)).lower(), self._carpeta(r)))      # (estable: dentro de cada carpeta queda el orden guardado)
                    self._plegadas.update(c["tot"])
            lim = 0.012 if _hay_musica() else 0.03                                # ~30 ms por tanda (12 ms si suena música: así el audio no se corta)
            while c["i"] < len(rutas) and time.perf_counter() - t0 < lim:
                p = rutas[c["i"]]; c["i"] += 1
                if not p.lower().endswith(SUPPORTED):
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                clave = os.path.normcase(os.path.abspath(p))
                if clave in self._rutas:
                    continue
                # (la lista guardada se carga tal cual: si agregaste una copia a propósito -«Agregar las copias igual»- tiene que seguir ahí
                #  al reiniciar; los repetidos solo se filtran al ARRASTRAR temas nuevos)
                self._rutas.add(clave)
                if c["agr"]:
                    k = self._carpeta(p)
                    if k != c["ult"]:
                        c["ult"] = k
                        self._agregar_cab(k, os.path.dirname(os.path.abspath(p)), c["tot"].get(k, 1))
                self.addItem(self._crear_item(p, clave))
                if c["agr"] and c["ult"] in self._plegadas:
                    self.setRowHidden(self.count() - 1, True)
                c["n"] += 1
        finally:
            self._cargando = prev
            self.setUpdatesEnabled(True)
        self.carga_progreso.emit(c["i"], len(c["rutas"]))
        if c["n"] and not c["primera"]:
            c["primera"] = True
            self.primera_tanda.emit()
        if c["i"] < len(c["rutas"]):
            QTimer.singleShot(4 if _hay_musica() else 0, self._cargar_tanda)
            return
        self._carga = None                                   # todos los temas ya están en la lista
        log("biblioteca", f"+{c['n']} tema(s) agregado(s) · total {self.count()}")
        self._precargando = True
        if self.agrupado and self.count() and not self._es_cab(self.item(0)):
            self._poner_cab()                                # las carpetas se ven YA (armarlas lleva menos de 1 s); el BPM, el tono y las duraciones llegan después
        self.lista_cargada.emit()
        threading.Thread(target=self._precargar_info, args=(self.todas(),), daemon=True).start()    # BPM / tono de cada tema, sin trabar la ventana

    def _precargar_info(self, rutas):
        """Lee el BPM/tono de cada tema (miles de .json) en un hilo. Leer un .json grande retiene Python unos milisegundos: si suena música,
        la lectura va de a poco (soltando Python seguido) para que el audio no se corte ('rrrr'); sin música va a toda velocidad."""
        try:
            try:
                from setup import hilos_analisis as _ha
            except Exception:
                _ha = None
            self._idx_completo = False                       # (temas agregados que ya tenían .json de antes: se leen una vez y pasan al índice)
            for n, r in enumerate(rutas):
                self.info(r)
                if len(self._lote_idx) >= 200:
                    self._guardar_lote_idx()                 # lo leído de los .json pasa al índice (así la próxima vez no hay que leerlos)
                leyo_json = bool(self._lote_idx)
                if _ha is not None and _ha._SONANDO:
                    if leyo_json or not self._idx_ok:
                        time.sleep(0.008)                    # (desde el índice no se lee nada: no hace falta frenar)
                elif n % 8 == 0 and (leyo_json or not self._idx_ok):
                    time.sleep(0.001)
            self._guardar_lote_idx()
            if self._idx_ok:
                self._idx_completo = True                    # todos los temas de la lista ya pasaron por el índice
        except Exception:
            pass
        self._precarga_lista.emit()                          # (vuelve al hilo de la ventana)

    def _fin_precarga(self):
        self._precargando = False
        self._cargando_lib = False
        if self.agrupado:                                    # los títulos de carpeta suman la duración de sus temas: recién ahora, con todo leído
            self._quitar_cab(); self._poner_cab()
        self._refrescar_textos()                             # ya con BPM y tono de cada tema
        self.cambio.emit()
        self.nuevos.emit()                                   # arranca el análisis de los que faltan
        self.carga_terminada.emit()

    # ---------- drag & drop ----------
    def mimeData(self, items):
        """Para un tema: la ruta del archivo (como antes). Para un encabezado de carpeta: la ruta ORIGINAL de la
        carpeta → al soltarla en Explorer se copia la carpeta entera con todo su contenido."""
        md = QMimeData()
        urls = []
        for i in items:
            if self._es_cab(i):
                ruta = i.data(self.ROL_CARPETA_ORIG) or i.data(Qt.ItemDataRole.UserRole)
            else:
                ruta = i.data(Qt.ItemDataRole.UserRole)
            if ruta:
                urls.append(QUrl.fromLocalFile(ruta))
        md.setUrls(urls)
        return md

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if e.source() is self or not e.mimeData().hasUrls():
            e.ignore(); return
        rutas = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        n = self.agregar(rutas)
        e.acceptProposedAction()
        r = getattr(self, "ultimo_resumen", None) or {}
        if n == 0 and (r.get("ya_estaban") or r.get("repetidos")):
            QTimer.singleShot(0, lambda: self._avisar_nada_nuevo(rutas, r))      # (fuera del evento de soltar)

    def _avisar_nada_nuevo(self, rutas, r):
        """Soltaron una carpeta/temas y no se agregó nada porque ya estaban: se explica claro y se ofrece agregar las copias igual."""
        ya, rep = int(r.get("ya_estaban", 0)), list(r.get("repetidos") or [])
        partes = []
        if ya:
            partes.append(f"• {ya} tema(s) ya estaban en la lista (el mismo archivo).")
        if rep:
            partes.append(f"• {len(rep)} tema(s) son copias de otros que ya están (mismo nombre y tamaño, en otra carpeta).")
        caja = QMessageBox(self)
        caja.setWindowTitle("No se agregó nada nuevo")
        caja.setIcon(QMessageBox.Icon.Information)
        caja.setText("Lo que arrastraste ya está en la lista:\n\n" + "\n".join(partes))
        b_si = None
        if rep:
            b_si = caja.addButton("Agregar las copias igual", QMessageBox.ButtonRole.AcceptRole)
        caja.addButton("Cerrar", QMessageBox.ButtonRole.RejectRole)
        caja.exec()
        if b_si is not None and caja.clickedButton() is b_si:
            self.agregar(rep, permitir_repetidos=True)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:                                   # lista vacía: indicación en el centro
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            f = QFont(self.font()); f.setPointSizeF(f.pointSizeF() * 1.25); f.setBold(True)
            p.setFont(f); p.setPen(QColor(TEMA["suave"]))
            r = self.viewport().rect().adjusted(16, 0, -16, 0)
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), "📂\n\nArrastrá carpetas o archivos acá")
            p.end()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete:
            self.quitar_seleccion()
        else:
            super().keyPressEvent(e)
