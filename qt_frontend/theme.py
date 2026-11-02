'''Native palette and QSS matching the supplied light/dark reference.'''
from PySide6.QtGui import QColor,QPalette,QFont
from PySide6.QtWidgets import QApplication
DARK={'bg':'#0b1220','surface':'#111a2b','field':'#0d1728','line':'#243249','blue':'#6f9bff','data':'#4cc3d9','amber':'#e0a84a','warning':'#e0a84a','success':'#5fd08a','danger':'#ff6b6b','text':'#d7e0ee','muted':'#9aa8c2','cursor':'#d7e0ee','plot':'#0a1322','plot_border':'#293b51','chart_grid':'#233348','hatch':'#3c4963','button_hover':'#1b2a41','border_hover':'#3a5170','toast':'#1b2a41','accent':'#1e304d','accent_border':'#425d86'}
LIGHT={'bg':'#edf1f5','surface':'#fafbfd','field':'#f0f3f7','line':'#d4dde7','blue':'#326bba','data':'#00695f','amber':'#94600d','warning':'#855300','success':'#22c55e','danger':'#b3263a','text':'#233249','muted':'#63748a','cursor':'#526071','plot':'#e4edf7','plot_border':'#b7c5d5','chart_grid':'#c3cfdd','hatch':'#b6c1cf','button_hover':'#e2e9f2','border_hover':'#a9b9cc','toast':'#ffffff','accent':'#dbe6f6','accent_border':'#a9b9cc'}
MAP_COLORS=['#2b305b','#2c6989','#4cc3d9','#caebf0']
_CURRENT='light'
def palette(mode=None):
    return dict(LIGHT if (mode or _CURRENT)=='light' else DARK)
def current_mode():
    return _CURRENT
def map_qcolors():
    return [QColor(value) for value in MAP_COLORS]
def map_color(fraction):
    fraction=max(0.0,min(1.0,float(fraction))); stops=MAP_COLORS; x=fraction*(len(stops)-1); index=min(len(stops)-2,int(x)); local=x-index
    a=QColor(stops[index]); b=QColor(stops[index+1])
    return QColor(int(a.red()+(b.red()-a.red())*local),int(a.green()+(b.green()-a.green())*local),int(a.blue()+(b.blue()-a.blue())*local))
QSS='''
QWidget{color:%(text)s;font:14px 'Segoe UI';}
QMainWindow#MainWindow,QWidget#acquisitionPage,QWidget#analysisPage,QWidget#calibrationPage,QWidget#analysisResults,QWidget#centralStack{background:%(bg)s;}
QWidget#top,QWidget#subnav,QFrame#foot{background:%(surface)s;}
QWidget#top{border-bottom:1px solid %(line)s;}QWidget#subnav{border-bottom:1px solid %(line)s;}
QLabel{background:transparent;}QLabel[muted=true]{color:%(muted)s;}QLabel[h2=true]{font-size:15px;font-weight:600;}QLabel[mono=true]{font-family:Consolas;}
QLabel#runStatus{color:%(muted)s;}QLabel#sampleName{color:%(muted)s;font-size:12px;}QLabel#progressText,QLabel#remaining,QLabel#stageStatus,QLabel#validation,QLabel#saveStatus,QLabel#fitStatus,QLabel#calFeedback{color:%(muted)s;}
QLabel#validation,QLabel#calFeedback.error{color:%(danger)s;}QLabel#saturation,QLabel#calRoiLabel{color:%(warning)s;font-weight:600;}
QPushButton{background:%(field)s;border:1px solid %(line)s;border-radius:5px;min-height:32px;padding:0 11px;color:%(text)s;}
QPushButton:hover{background:%(button_hover)s;border-color:%(border_hover)s;}QPushButton:disabled{color:%(muted)s;}
QPushButton[role=primary]{background:%(blue)s;border-color:%(blue)s;color:%(bg)s;font-weight:600;}QPushButton[role=primary]:hover{background:%(blue)s;border-color:%(blue)s;}
QPushButton#stop:enabled{background:%(danger)s;border-color:%(danger)s;color:%(bg)s;font-weight:600;}
QPushButton#run,QPushButton#footExport,QPushButton#calExport,QPushButton#calApply{font-weight:600;}
QPushButton[role=accent]{background:%(accent)s;border-color:%(accent_border)s;color:%(text)s;font-weight:600;}
QPushButton[role=dangerText]{color:%(danger)s;background:transparent;border-color:transparent;}
QPushButton#connectionButton{height:26px;min-height:26px;padding:0 7px;background:transparent;border-color:transparent;color:%(muted)s;}
QPushButton#connectionButton[state=connected]{color:%(data)s;}QPushButton#connectionButton[state=partial]{color:%(warning)s;}QPushButton#connectionButton[state=simulated]{color:%(warning)s;}
QPushButton#hardwareMode{background:%(amber)s;color:%(bg)s;font-weight:600;font-size:11px;min-height:27px;padding:0 8px;}QPushButton#theme{min-height:27px;min-width:32px;}
QPushButton[nav=true]{background:transparent;border:0;border-radius:0;color:%(muted)s;min-height:39px;padding:0 2px;}QPushButton[nav=true][active=true]{color:%(text)s;border-bottom:2px solid %(blue)s;}
QPushButton[selected=true]{background:%(blue)s;color:%(bg)s;border-color:%(blue)s;}
QWidget#segmented{background:%(field)s;border:1px solid %(line)s;border-radius:5px;}QWidget#segmented QPushButton{min-height:25px;border:0;background:transparent;color:%(muted)s;padding:0 7px;}QWidget#segmented QPushButton[selected=true]{background:%(blue)s;color:%(bg)s;}QWidget#segmented QPushButton:disabled{color:%(muted)s;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{background:%(field)s;border:1px solid %(line)s;border-radius:5px;min-height:32px;padding:0 7px;color:%(text)s;selection-background-color:%(blue)s;}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus{border-color:%(blue)s;}QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled{color:%(muted)s;}
QLineEdit#delay{border:0;border-bottom:1px dashed %(line)s;border-radius:0;background:transparent;font:28px Consolas;min-height:34px;}
QLineEdit[mono=true]{font-family:Consolas;}QLineEdit:read-only{color:%(muted)s;}
QAbstractSpinBox::up-button,QAbstractSpinBox::down-button{width:0;height:0;border:0;}QComboBox::drop-down{border:0;width:18px;}
QComboBox QAbstractItemView{background:%(surface)s;color:%(text)s;selection-background-color:%(blue)s;}
QCheckBox{color:%(muted)s;spacing:6px;}QCheckBox::indicator{width:14px;height:14px;border:1px solid %(line)s;border-radius:3px;background:%(field)s;}QCheckBox::indicator:checked{background:%(blue)s;border-color:%(blue)s;}
QFrame#sidebar,QFrame#analysisControls,QFrame#calibrationControls{background:%(surface)s;border:1px solid %(line)s;border-radius:7px;}QScrollArea{border:0;background:transparent;}QScrollArea>QWidget>QWidget{background:transparent;}
QFrame#section{border:0;border-bottom:1px solid %(line)s;background:transparent;}QFrame#section:last-child{border-bottom:0;}
QFrame#panel{background:%(surface)s;border:1px solid %(line)s;border-radius:7px;}QWidget#panelHead{background:transparent;border:0;}QWidget#panelBody{background:transparent;}
QPushButton#summary{background:transparent;border:1px solid %(line)s;color:%(muted)s;text-align:left;min-height:32px;padding:0 8px;}
QWidget#analysisResidualWrap{border-top:1px solid %(line)s;}QWidget#analysisResults{background:transparent;}
QTableWidget,QTableView{background:%(surface)s;border:0;gridline-color:%(line)s;color:%(text)s;selection-background-color:%(accent)s;selection-color:%(text)s;}
QHeaderView::section{background:%(surface)s;color:%(muted)s;border:0;border-bottom:1px solid %(line)s;padding:6px;font-weight:400;}
QTableWidget::item{border-bottom:1px solid %(line)s;padding:4px;}QTableCornerButton::section{background:%(surface)s;border:0;}
QProgressBar{background:%(line)s;border:0;border-radius:2px;max-height:5px;min-height:5px;}QProgressBar::chunk{background:%(blue)s;border-radius:2px;}QProgressBar[locked=true]{background:%(field)s;}
QScrollBar:vertical{background:transparent;width:9px;margin:0;}QScrollBar::handle:vertical{background:%(line)s;border-radius:4px;min-height:20px;}QScrollBar::add-line,QScrollBar::sub-line{height:0;}
QScrollBar:horizontal{background:transparent;height:9px;}QScrollBar::handle:horizontal{background:%(line)s;border-radius:4px;min-width:20px;}
QToolTip{background:%(surface)s;color:%(text)s;border:1px solid %(line)s;padding:5px;}
QDialog{background:%(surface)s;color:%(text)s;}QMessageBox{background:%(surface)s;}
QFrame#axisInlineEditor{background:%(surface)s;border:1px solid %(plot_border)s;border-radius:4px;}QLineEdit#axisInlineValue{min-height:26px;}
QFrame#calMetrics{background:%(plot)s;border:1px solid %(plot_border)s;border-radius:4px;}QFrame#calMetrics QLabel{font-size:11px;color:%(muted)s;}QFrame#calMetrics QLabel[value=true]{font-family:Consolas;font-size:11px;color:%(text)s;}
QWidget#startupOverlay{background:rgba(6,11,20,110);}QFrame#startupCard{background:%(surface)s;border:1px solid %(line)s;border-radius:8px;}QLabel#startupTitle{font-size:17px;font-weight:600;}QLabel#startupMessage{color:%(muted)s;}
QLabel#toast{background:%(toast)s;border:1px solid %(border_hover)s;border-radius:5px;padding:8px 16px;color:%(text)s;}
QWidget#deviceCard{border-top:1px solid %(line)s;}QLabel[error=true]{color:%(danger)s;}
'''
def qss(mode=None):
    return QSS % palette(mode)
def apply_theme(app,mode):
    global _CURRENT
    _CURRENT='dark' if mode=='dark' else 'light'
    app.setStyleSheet(qss(_CURRENT))
    colors=palette(_CURRENT); p=QPalette(); p.setColor(QPalette.Window,QColor(colors['bg'])); p.setColor(QPalette.Base,QColor(colors['field'])); p.setColor(QPalette.AlternateBase,QColor(colors['surface'])); p.setColor(QPalette.Text,QColor(colors['text'])); p.setColor(QPalette.WindowText,QColor(colors['text'])); p.setColor(QPalette.Button,QColor(colors['field'])); p.setColor(QPalette.ButtonText,QColor(colors['text'])); p.setColor(QPalette.Highlight,QColor(colors['blue'])); p.setColor(QPalette.HighlightedText,QColor(colors['bg'])); app.setPalette(p)
def apply_to_app(mode='light'):
    app=QApplication.instance()
    if app is not None: apply_theme(app,mode)
