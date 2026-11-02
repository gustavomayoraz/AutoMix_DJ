"""AutoMix — análisis de la biblioteca en un programa aparte (setup/AnalizadorAutoMix.py).
Este módulo lo usan los dos lados: el reproductor (lanzar_aparte / aparte_activo) y el programa aparte (main)."""
import os
import subprocess
import sys
import time
from pathlib import Path
from setup.base import CACHE_DIR, ROOT

RAIZ = ROOT                  # carpeta del programa (la de datos ya no está dentro de ella)
LISTA = CACHE_DIR / "lista_analisis_aparte.txt"
LOCK = CACHE_DIR / ".analizador_aparte.lock"
LATIDO_S = 2.0              # el programa aparte "toca" el archivo cada tanto: si pasan más de VIVO_S sin tocarlo, se lo da por cerrado
VIVO_S = 12.0


def aparte_activo():
    """True si hay un analizador aparte corriendo (por su archivo de latido, sirve aunque el reproductor se haya reiniciado)."""
    try:
        return (time.time() - LOCK.stat().st_mtime) < VIVO_S
    except OSError:
        return False


def lanzar_aparte(rutas, suave=False, forzar=False):
    """Escribe la lista de temas y abre el analizador aparte. Devuelve el proceso (subprocess.Popen)."""
    if aparte_activo():
        raise OSError("ya hay un analizador aparte abierto (esperá a que termine o cerralo)")
    LISTA.write_text("\n".join(rutas), encoding="utf-8")
    exe = Path(sys.executable)
    if os.name == "nt":
        pw = exe.with_name("pythonw.exe")
        if pw.exists():
            exe = pw                                                      # sin ventana negra de consola
    cmd = [str(exe), str(RAIZ / "setup" / "AnalizadorAutoMix.py"), "--lista", str(LISTA)]
    if suave:
        cmd.append("--suave")
    if forzar:
        cmd.append("--forzar")
    return subprocess.Popen(cmd, cwd=str(RAIZ), creationflags=0x08000000 if os.name == "nt" else 0)


def _traer_reproductor():
    """Trae al frente la ventana del reproductor (aunque esté minimizada o detrás). Si no está abierto, lo abre.
    En Windows busca una ventana visible de OTRO proceso cuyo título empiece con 'AutoMix' (la más grande: la principal)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes as w
            u = ctypes.windll.user32
            propio = os.getpid()
            hallados = []
            enum = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

            def _cb(h, _):
                if not u.IsWindowVisible(h):
                    return True
                n = u.GetWindowTextLengthW(h)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, buf, n + 1)
                if not buf.value.startswith("AutoMix"):
                    return True
                pid = w.DWORD(0)
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if pid.value == propio:
                    return True
                r = w.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
                hallados.append((bool(u.IsIconic(h)), area, h))
                return True
            u.EnumWindows(enum(_cb), 0)
            if hallados:
                h = max(hallados, key=lambda t: t[1])[2]                             # la más grande: la ventana principal
                u.ShowWindow(h, 9)                                                   # SW_RESTORE
                u.SetForegroundWindow(h)
                return True
        except (OSError, AttributeError):
            pass
    exe = Path(sys.executable)
    if os.name == "nt" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    try:
        subprocess.Popen([str(exe), str(RAIZ / "AutoMix.py")], cwd=str(RAIZ), creationflags=0x08000000 if os.name == "nt" else 0)
        return True
    except OSError:
        return False


def _tomar_lock():
    if aparte_activo():
        return False
    try:
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def main(argv):
    lista = None
    for i, a in enumerate(argv):
        if a == "--lista" and i + 1 < len(argv):
            lista = Path(argv[i + 1])
    suave, forzar = "--suave" in argv, "--forzar" in argv
    if lista is None or not lista.is_file():
        print("[aparte] falta --lista o el archivo no existe")
        return 2
    rutas = [r.strip() for r in lista.read_text(encoding="utf-8").splitlines() if r.strip()]
    rutas = [r for r in rutas if os.path.isfile(r)]
    if not rutas:
        print("[aparte] no hay temas para analizar")
        return 0
    if not _tomar_lock():
        print("[aparte] ya hay un analizador aparte corriendo")
        return 3
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from setup.dialogo_analisis import DialogoAnalisis
    from setup.hilos_analisis import AnalizadorBiblioteca
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        from setup.icono import aplicar_icono
        aplicar_icono(app)
    except Exception:
        pass
    n = AnalizadorBiblioteca._limitar_por_ram(max(1, min(10, (os.cpu_count() or 2) - 2))) if not suave else AnalizadorBiblioteca._num_trabajadores()
    pie = ("Podés seguir usando el reproductor: el análisis se hace aparte y lo guardado queda en la carpeta cache."
           if not suave else "Modo suave (prioridad baja): se puede usar el reproductor mientras tanto, pero tarda más.")
    dlg = DialogoAnalisis(None, len(rutas), n, permitir_fondo=False, pie=pie, volver=_traer_reproductor)
    dlg.setWindowTitle("AutoMix 🎚️ — Analizador aparte")
    dlg.show()
    a = AnalizadorBiblioteca(rutas, forzar=forzar, modo_max=not suave, aparte=True)
    a.progreso.connect(dlg.poner)
    a.en_curso.connect(dlg.poner_actual)
    dlg.cancelar.connect(lambda: setattr(a, "cancelar", True))
    latido = QTimer(); latido.setInterval(int(LATIDO_S * 1000))
    def _latir():
        try:
            LOCK.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
    latido.timeout.connect(_latir); latido.start()
    def _fin():
        latido.stop()
        cancelado = a.cancelar
        dlg.lbl_vel.setText("⏹ Detenido: lo ya analizado quedó guardado." if cancelado else "✅ Listo: todo quedó guardado en la carpeta cache.")
        dlg.btn.setEnabled(False)
        try:
            LOCK.unlink()
        except OSError:
            pass
        QTimer.singleShot(1500, lambda: (dlg.btn.setEnabled(False), dlg.close(), app.quit()))
    a.terminado.connect(_fin)
    a.start()
    app.exec()
    try:
        LOCK.unlink()
    except OSError:
        pass
    return 0
