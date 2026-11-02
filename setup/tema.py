"""AutoMix — colores y estilo general (QSS)."""
import sys
from PySide6.QtGui import QColor
from setup.base import CACHE_DIR


# =====================================================================
# 3) DECK
# =====================================================================
# =====================================================================
# 0.5) TEMA VISUAL (oscuro, con acentos de neón)
# =====================================================================
TEMA = dict(
    fondo="#06070c", bg0="#06070c", bg1="#0b0e16", bg2="#121726", bg3="#1a2133", linea="#252d44",
    texto="#e9eeff", suave="#8892b0", cian="#22d3ff", magenta="#ff3d9a", verde="#2cff9a",
    ambar="#ffb82e", rojo="#ff3b4e", violeta="#8b5cf6",
)


COLOR_DECK_A = TEMA["cian"]


COLOR_DECK_B = TEMA["magenta"]




def _rgba(hexcolor, a):
    c = QColor(hexcolor)
    return f"rgba({c.red()},{c.green()},{c.blue()},{int(a)})"




def aplicar_tema(app):
    """Estilo Fusion oscuro + hoja de estilo global. No toca tamaños de letra (eso lo maneja la escala de la ventana)."""
    from PySide6.QtGui import QPalette
    T = TEMA
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(T["fondo"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(T["texto"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(T["bg1"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#0e1322"))
    pal.setColor(QPalette.ColorRole.Text, QColor(T["texto"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(T["bg3"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(T["texto"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(T["cian"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(T["bg0"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(T["bg2"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(T["texto"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#5b6688"))
    for rol in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, rol, QColor("#566082"))
    app.setPalette(pal)
    iconos = {}
    try:                                   # tilde y flechitas como SVG (se escriben una vez en cache/ui)
        d = CACHE_DIR / "ui"; d.mkdir(parents=True, exist_ok=True)
        svgs = {
            "check": '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path d="M3.6 8.6 L6.8 11.6 L12.6 4.6" fill="none" stroke="#04131c" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
            "arriba": '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><path d="M1 7 L5 2.5 L9 7 Z" fill="#9fb0dd"/></svg>',
            "abajo": '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><path d="M1 3 L5 7.5 L9 3 Z" fill="#9fb0dd"/></svg>',
        }
        for k, v in svgs.items():
            f = d / f"{k}.svg"
            if not f.exists() or f.read_text(encoding="utf-8") != v:
                f.write_text(v, encoding="utf-8")
            iconos[k] = f.as_posix()
    except Exception:
        pass
    img = lambda k: (f"image:url(\"{iconos[k]}\");" if k in iconos else "")
    if sys.platform.startswith("win"):
        f = app.font(); f.setFamily("Segoe UI"); app.setFont(f)
    app.setStyleSheet(f"""
QToolTip {{ background:{T['bg2']}; color:{T['texto']}; border:1px solid {T['cian']}; padding:5px; border-radius:4px; }}
QSplitter::handle {{ background:{T['fondo']}; }}
QSplitter::handle:hover {{ background:{T['cian']}; }}

QGroupBox {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1b243b, stop:1 #131a2c);
            border:1px solid {T['linea']}; border-radius:12px; margin-top:12px; padding-top:6px; }}
QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top left; left:14px; padding:0 8px; color:{T['cian']}; font-weight:bold; }}
QFrame#panelcruce {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1b243b, stop:1 #131a2c);
            border:1px solid {T['linea']}; border-radius:12px; }}

QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2a3452, stop:1 #171d31);
              border:1px solid #334066; border-radius:9px; padding:4px 10px; color:{T['texto']}; }}
QPushButton:hover {{ border-color:{T['cian']}; background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #34406a, stop:1 #1c2440); }}
QPushButton:pressed {{ background:#0b0f1c; border-color:{T['cian']}; padding-top:5px; }}
QPushButton:checked {{ border-color:{T['ambar']}; background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #4a3a12, stop:1 #2a2108); }}
QPushButton:disabled {{ color:#566082; background:#101524; border-color:#1d2438; }}
QPushButton[rol="play"] {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #22d98a, stop:1 #0c9a5e); border:1px solid #5dffb4; color:#032015; }}
QPushButton[rol="play"]:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #4cf0a6, stop:1 #12b374); }}
QPushButton[rol="pausa"] {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #3a3320, stop:1 #211c10); border:1px solid #8a7430; color:#ffd36a; }}
QPushButton[rol="stop"] {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #432030, stop:1 #26111b); border:1px solid #a8344f; color:#ff7a93; }}
QPushButton[rol="play"]:pressed, QPushButton[rol="pausa"]:pressed, QPushButton[rol="stop"]:pressed {{ background:#0b0f1c; }}

QSlider::groove:horizontal {{ height:6px; background:#123a30; border:1px solid #1f5a48; border-radius:4px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0f7fa6, stop:1 {T['cian']}); border-radius:4px; }}
QSlider::add-page:horizontal {{ background:transparent; }}
QSlider::handle:horizontal {{ width:14px; height:14px; margin:-6px 0; border-radius:8px; border:2px solid {T['cian']};
            background: qradialgradient(cx:0.5, cy:0.35, radius:0.9, fx:0.5, fy:0.25, stop:0 #ffffff, stop:1 #aebcdc); }}
QSlider::handle:horizontal:hover {{ border-color:#ffffff; background:#ffffff; }}
QSlider::sub-page:horizontal:disabled {{ background:#2a3350; }}
QSlider::handle:horizontal:disabled {{ border-color:#3a4566; background:#59627f; }}

QCheckBox {{ spacing:7px; }}
QCheckBox::indicator {{ width:16px; height:16px; border-radius:5px; border:1px solid #3d4a73; background:#080b14; }}
QCheckBox::indicator:hover {{ border-color:{T['cian']}; }}
QCheckBox::indicator:checked {{ {img('check')} border:1px solid #7be8ff; background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {T['cian']}, stop:1 #0f7fa6); }}

QLineEdit, QSpinBox {{ background:#080b14; border:1px solid {T['linea']}; border-radius:8px; padding:3px 8px;
            selection-background-color:{T['cian']}; selection-color:{T['bg0']}; }}
QLineEdit:focus, QSpinBox:focus {{ border-color:{T['cian']}; }}
QSpinBox {{ padding-right:20px; }}
QSpinBox::up-button {{ subcontrol-origin:border; subcontrol-position:top right; width:20px; border:none; border-top-right-radius:7px; background:#161d31; }}
QSpinBox::down-button {{ subcontrol-origin:border; subcontrol-position:bottom right; width:20px; border:none; border-bottom-right-radius:7px; background:#161d31; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background:#26304f; }}
QSpinBox::up-arrow {{ {img('arriba')} width:10px; height:10px; }}
QSpinBox::down-arrow {{ {img('abajo')} width:10px; height:10px; }}

QListWidget {{ background:#080b14; border:1px solid {T['linea']}; border-radius:10px; alternate-background-color:#0c111f; outline:0; padding:3px; }}
QListWidget::item {{ padding:3px 6px; border-radius:5px; }}
QListWidget::item:selected {{ border:2px solid #ffffff; color:#ffffff; }}

QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar::handle:vertical {{ background:#2a3554; border-radius:4px; min-height:26px; }}
QScrollBar::handle:vertical:hover {{ background:{T['cian']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
QScrollBar:horizontal {{ background:transparent; height:10px; margin:2px; }}
QScrollBar::handle:horizontal {{ background:#2a3554; border-radius:4px; min-width:26px; }}
QScrollBar::handle:horizontal:hover {{ background:{T['cian']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background:transparent; }}
QFrame[frameShape="4"] {{ color:{T['linea']}; }}

QMessageBox {{ background:#1f3b63; border:2px solid #4fd1ff; }}
QMessageBox QLabel {{ color:#ffffff; background:transparent; }}
QMessageBox QPushButton {{ background:#2f5f99; color:#ffffff; border:1px solid #7fb4ee; border-radius:8px; padding:7px 8px; }}
QMessageBox QPushButton:hover {{ background:#3b74b8; border:1px solid #4fd1ff; }}
QMessageBox QPushButton:default {{ background:#1f9d55; border:1px solid #6fe3a1; }}
QMessageBox QPushButton:default:hover {{ background:#27b865; }}
""")
