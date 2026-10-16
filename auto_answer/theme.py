from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPen
from PySide6.QtCore import Qt

STYLE = """
QWidget {font-family:'Microsoft YaHei UI'; font-size:13px; color:#18323C;}
QMainWindow, QWidget#shell {background:#F5F4EF;}
QWidget#sidebar {background:#142D35;}
QLabel#brand {color:#F7FAF9; font-size:23px; font-weight:700;}
QLabel#brandSub {color:#A5BCBD; font-size:12px;}
QLabel#eyebrow {color:#0F766E; font-size:12px; font-weight:700;}
QLabel#title {font-size:30px; font-weight:700; color:#142D35;}
QLabel#subtitle {color:#647A80; font-size:13px;}
QLabel#sectionTitle {font-size:17px; font-weight:700;}
QLabel#muted {color:#61757D;}
QLabel#status {background:#E1F1EC; color:#145E54; border:1px solid #C1DDD2; border-radius:9px; padding:12px;}
QFrame#card {background:#FFFFFF; border:1px solid #DDE3DE; border-radius:13px;}
QFrame#preview {background:#142D35; border:0; border-radius:12px;}
QLabel#previewLabel {color:#90B8B5; font-size:12px;}
QLabel#previewAnswer {color:#ECFDF5; font-size:24px;}
QPushButton {background:#FFFFFF; border:1px solid #CEDAD5; border-radius:8px; padding:9px 15px; min-height:20px; font-weight:600;}
QPushButton:hover {background:#EBF5F0; border-color:#8AB9AD;}
QPushButton:pressed {background:#D9EAE3;}
QPushButton:disabled {color:#99A7A6; background:#EEF0ED; border-color:#E4E7E3;}
QPushButton#primary {background:#0F766E; border-color:#0F766E; color:#FFFFFF;}
QPushButton#primary:hover {background:#0B625C;}
QPushButton#danger {color:#A74232; border-color:#E1C9C3;}
QPushButton#nav {text-align:left; background:transparent; border:0; border-radius:8px; color:#B9CDCE; padding:12px 15px; font-size:14px;}
QPushButton#nav:hover {background:#25434B; color:#FFFFFF;}
QPushButton#nav:checked {background:#E5F2E9; color:#145E54;}
QLineEdit,QSpinBox,QComboBox,QFontComboBox {background:#FBFCFA; border:1px solid #CFD9D3; border-radius:7px; padding:8px; min-height:24px; selection-background-color:#0F766E;}
QLineEdit:focus,QSpinBox:focus,QComboBox:focus {border:1px solid #0F766E;}
QComboBox::drop-down {border:0; width:23px;}
QTextEdit,QListWidget {background:#FBFCFA; border:1px solid #D8E0D9; border-radius:9px; padding:8px;}
QListWidget::item {padding:12px; border-bottom:1px solid #E3E9E3;}
QListWidget::item:selected {background:#DDF0E8; color:#143F39; border-radius:6px;}
QCheckBox {spacing:9px; padding:5px 0;}
QCheckBox::indicator {width:18px; height:18px; border:1px solid #AEBFB6; border-radius:5px; background:#FFFFFF;}
QCheckBox::indicator:checked {background:#0F766E; border:1px solid #0F766E;}
QScrollArea {background:transparent; border:0;}
QScrollArea > QWidget > QWidget {background:transparent;}
QScrollBar:vertical {background:transparent; width:8px; margin:0;}
QScrollBar::handle:vertical {background:#BCCBC2; min-height:25px; border-radius:4px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QToolTip {background:#173A40; color:#FFFFFF; border:0; padding:7px;}
"""


def app_icon() -> QIcon:
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#0F766E"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 120, 120, 28, 28)
    painter.setPen(QPen(QColor("#E6FFF4"), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawLine(32, 66, 54, 87)
    painter.drawLine(54, 87, 96, 42)
    painter.end()
    return QIcon(pixmap)
