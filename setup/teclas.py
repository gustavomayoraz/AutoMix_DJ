"""AutoMix — teclas globales F2 y flechas ↑ / ↓ (volumen) (Windows) y flechas ← / → sobre el slider que está bajo el mouse."""
import sys
from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QApplication, QLineEdit, QPlainTextEdit, QTextEdit


# =====================================================================
# 6) VENTANA PRINCIPAL
# =====================================================================
class TeclaGlobalF2(QAbstractNativeEventFilter):
    """Tecla F2 "global" en Windows (RegisterHotKey): funciona aunque el reproductor esté en segundo plano."""
    ID = 0xA7F2
    WM_HOTKEY = 0x0312
    VK_F2 = 0x71

    def __init__(self, accion):
        super().__init__()
        self.accion = accion
        self.activa = False

    def registrar(self):
        if sys.platform != "win32" or self.activa:
            return self.activa
        try:
            import ctypes
            if ctypes.windll.user32.RegisterHotKey(None, self.ID, 0x4000, self.VK_F2):   # 0x4000 = sin repetir al mantenerla
                QCoreApplication.instance().installNativeEventFilter(self)
                self.activa = True
        except Exception:
            self.activa = False
        return self.activa

    def quitar(self):
        if not self.activa:
            return
        try:
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(None, self.ID)
            QCoreApplication.instance().removeNativeEventFilter(self)
        except Exception:
            pass
        self.activa = False

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == self.WM_HOTKEY and msg.wParam == self.ID:
                    QTimer.singleShot(0, self.accion)
        except Exception:
            pass
        return False, 0


class TeclasGlobalesVolumen(QAbstractNativeEventFilter):
    """Flechas ↑ (sube) y ↓ (baja) como teclas "globales": suben/bajan el volumen aunque el programa esté en segundo plano.
    Se dejan repetir: mantenerlas apretadas sigue moviendo el volumen."""
    ID_MAS = 0xA7F3
    ID_MENOS = 0xA7F4
    WM_HOTKEY = 0x0312
    VK_ADD = 0x26          # flecha arriba (sube el volumen)
    VK_SUBTRACT = 0x28     # flecha abajo (baja el volumen)

    def __init__(self, accion):
        super().__init__()
        self.accion = accion          # accion(delta): +1 sube, -1 baja
        self.activa = False

    def registrar(self):
        if sys.platform != "win32" or self.activa:
            return self.activa
        try:
            import ctypes
            u = ctypes.windll.user32
            ok1 = u.RegisterHotKey(None, self.ID_MAS, 0, self.VK_ADD)
            ok2 = u.RegisterHotKey(None, self.ID_MENOS, 0, self.VK_SUBTRACT)
            if ok1 or ok2:
                QCoreApplication.instance().installNativeEventFilter(self)
                self.activa = True
                self._ok = (bool(ok1), bool(ok2))
        except Exception:
            self.activa = False
        return self.activa

    def quitar(self):
        if not self.activa:
            return
        try:
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(None, self.ID_MAS)
            ctypes.windll.user32.UnregisterHotKey(None, self.ID_MENOS)
            QCoreApplication.instance().removeNativeEventFilter(self)
        except Exception:
            pass
        self.activa = False

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == self.WM_HOTKEY:
                    if msg.wParam == self.ID_MAS:
                        QTimer.singleShot(0, lambda: self.accion(+1))
                    elif msg.wParam == self.ID_MENOS:
                        QTimer.singleShot(0, lambda: self.accion(-1))
        except Exception:
            pass
        return False, 0


class FlechasSobreSlider(QObject):
    """Flechas ← / → mueven el slider que está bajo el mouse (un paso por pulsación; mantenerlas sigue moviendo).
    Solo dentro de AutoMix (no son teclas globales). No se mete si se está escribiendo en un cuadro de texto o hay un menú abierto."""
    _TEXTO = (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)

    def instalar(self):
        app = QCoreApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def quitar(self):
        app = QCoreApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    @staticmethod
    def _slider_bajo_el_mouse():
        w = QApplication.widgetAt(QCursor.pos())
        while w is not None:
            if isinstance(w, QAbstractSlider):
                return w
            w = w.parentWidget()
        return None

    def eventFilter(self, obj, ev):
        try:
            if ev.type() != QEvent.Type.KeyPress or ev.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                return False
            if ev.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:        # solo la flecha sola (sin Ctrl / Alt / Shift)
                return False
            if QApplication.activePopupWidget() is not None or isinstance(QApplication.focusWidget(), self._TEXTO):
                return False
            sl = self._slider_bajo_el_mouse()
            if sl is None or not sl.isEnabled():
                return False
            sl.triggerAction(QAbstractSlider.SliderAction.SliderSingleStepAdd if ev.key() == Qt.Key.Key_Right
                             else QAbstractSlider.SliderAction.SliderSingleStepSub)
            return True
        except Exception:
            return False
