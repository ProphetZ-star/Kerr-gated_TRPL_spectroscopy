'''Native palette and QSS matching the supplied light/dark reference.'''
from PySide6.QtGui import QColor,QPalette,QFont
from PySide6.QtWidgets import QApplication
DARK={'bg':'#0b1220','surface':'#111a2b','field':'#0a1220','line':'#243249','blue':'#6f9bff','data':'#4cc3d9','amber':'#e0a84a','warning':'#e0a84a','success':'#5fd08a','danger':'#ff6b6b','text':'#d7e0ee','muted':'#9aa8c2','cursor':'#d7e0ee','plot':'#0a1322','plot_border':'#293b51','chart_grid':'#233348','hatch':'#3c4963','button_hover':'#1b2a41','border_hover':'#3a5170','toast':'#1b2a41','accent':'#1e304d','accent_border':'#425d86','track':'#070d18','chip':'#24385a','chip_text':'#e6edf8','btn':'#1a2638','btn_hover':'#22314a','btn_press':'#2a3b57','field_hover':'#0e182a','accent_hover':'#263b5e','blue_hover':'#86a9ff','invalid':'#3a1a22','overlay':'rgba(17,26,43,225)'}
LIGHT={'bg':'#edf1f5','surface':'#fafbfd','field':'#ebeff5','line':'#d4dde7','blue':'#326bba','data':'#00695f','amber':'#94600d','warning':'#855300','success':'#22c55e','danger':'#b3263a','text':'#233249','muted':'#63748a','cursor':'#526071','plot':'#e4edf7','plot_border':'#b7c5d5','chart_grid':'#c3cfdd','hatch':'#b6c1cf','button_hover':'#e2e9f2','border_hover':'#a9b9cc','toast':'#ffffff','accent':'#dbe6f6','accent_border':'#a9b9cc','track':'#dce3ec','chip':'#ffffff','chip_text':'#1f4f8f','btn':'#e3e9f1','btn_hover':'#d8e0ea','btn_press':'#ccd6e2','field_hover':'#e3e9f0','accent_hover':'#cddcf2','blue_hover':'#2a5ca3','invalid':'#f8e1e4','overlay':'rgba(250,251,253,225)'}
MAP_COLORS=['#2b305b','#2c6989','#4cc3d9','#caebf0']
SATURATED_COLOR='#ff7b1c'  # pixels at CCD full scale; warm so it never reads as a map / frame intensity
DARK['saturated']=LIGHT['saturated']=SATURATED_COLOR
_CURRENT='light'
LIGHT.update(text='#17263b',muted='#465b73',line='#b6c4d4')
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
QWidget#saturatedKey{background:transparent;}QFrame#saturatedSwatch{background:%(saturated)s;border-radius:2px;}QLabel#saturatedText{color:%(warning)s;font-size:10px;font-weight:600;}
QPushButton{background:%(btn)s;border:0;border-radius:5px;min-height:32px;padding:0 12px;color:%(text)s;}
QPushButton:hover{background:%(btn_hover)s;}QPushButton:pressed{background:%(btn_press)s;}QPushButton:disabled{color:%(muted)s;}
QPushButton[role=primary]{background:%(blue)s;color:%(bg)s;font-weight:600;}QPushButton[role=primary]:hover,QPushButton[role=primary]:pressed{background:%(blue_hover)s;}
QPushButton#stop:enabled{background:%(danger)s;color:%(bg)s;font-weight:600;}
QPushButton#run,QPushButton#footExport,QPushButton#calExport,QPushButton#calApply{font-weight:600;}
QPushButton[role=accent]{background:%(accent)s;color:%(chip_text)s;font-weight:600;}QPushButton[role=accent]:hover,QPushButton[role=accent]:pressed{background:%(accent_hover)s;}
QPushButton[role=dangerText]{color:%(danger)s;background:transparent;}
QPushButton[role=danger]{background:%(danger)s;color:%(bg)s;font-weight:600;}
QPushButton#connectionButton{height:26px;min-height:26px;padding:0 7px;background:transparent;color:%(muted)s;}QPushButton#connectionButton:hover{background:%(btn)s;color:%(text)s;}
QPushButton#connectionButton[state=connected]{color:%(data)s;}QPushButton#connectionButton[state=partial]{color:%(warning)s;}QPushButton#connectionButton[state=simulated]{color:%(warning)s;}
QComboBox#sourceMode{background:%(amber)s;color:%(bg)s;font-weight:600;font-size:11px;min-height:27px;padding:0 24px;}QPushButton#theme{min-height:27px;min-width:32px;}
QPushButton[nav=true]{background:transparent;border:0;border-radius:0;color:%(muted)s;min-height:39px;padding:0 2px;}QPushButton[nav=true]:hover{background:transparent;color:%(text)s;}QPushButton[nav=true][active=true]{color:%(text)s;border-bottom:2px solid %(blue)s;}
QPushButton[selected=true]{background:%(blue)s;color:%(bg)s;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{background:%(field)s;border:0;border-radius:5px;min-height:32px;padding:0 8px;color:%(text)s;selection-background-color:%(blue)s;}
QLineEdit:hover,QComboBox:hover,QSpinBox:hover,QDoubleSpinBox:hover{background:%(field_hover)s;}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus,QComboBox:on{background:%(surface)s;}
QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled{color:%(muted)s;background:%(field)s;}
QLineEdit#delay{border:0;border-bottom:1px dashed %(border_hover)s;border-radius:0;background:transparent;font:28px Consolas;min-height:34px;padding:0 2px;}QLineEdit#delay:hover{border-bottom:1px solid %(border_hover)s;}QLineEdit#delay:focus{border-bottom:1px solid %(blue)s;}
QLineEdit[mono=true]{font-family:Consolas;}QLineEdit:read-only{color:%(muted)s;}
QAbstractSpinBox::up-button,QAbstractSpinBox::down-button{width:0;height:0;border:0;}QComboBox::drop-down{border:0;width:18px;}
QComboBox QAbstractItemView{background:%(surface)s;color:%(text)s;selection-background-color:%(blue)s;}
QCheckBox{color:%(muted)s;spacing:6px;}QCheckBox::indicator{width:14px;height:14px;border:1px solid %(line)s;border-radius:3px;background:%(field)s;}QCheckBox::indicator:checked{background:%(blue)s;border-color:%(blue)s;}
QFrame#sidebar,QFrame#analysisControls,QFrame#calibrationControls{background:%(surface)s;border:0;border-radius:7px;}QScrollArea{border:0;background:transparent;}QScrollArea>QWidget>QWidget{background:transparent;}
QFrame#section{border:0;border-bottom:1px solid %(line)s;background:transparent;}QFrame#section:last-child{border-bottom:0;}
QFrame#panel{background:%(surface)s;border:0;border-radius:7px;}QWidget#panelHead{background:transparent;border:0;}QWidget#panelBody{background:transparent;}
QPushButton#summary{background:transparent;border:0;color:%(muted)s;text-align:left;min-height:32px;padding:0 8px;}QPushButton#summary:hover{color:%(text)s;}
QWidget#analysisResidualWrap{border-top:1px solid %(line)s;}QWidget#analysisResults{background:transparent;}
QTableWidget,QTableView{background:%(surface)s;border:0;gridline-color:%(line)s;color:%(text)s;selection-background-color:%(accent)s;selection-color:%(text)s;}
QHeaderView::section{background:%(surface)s;color:%(muted)s;border:0;border-bottom:1px solid %(line)s;padding:6px;font-weight:400;}
QTableWidget::item{border-bottom:1px solid %(line)s;padding:4px;}QTableCornerButton::section{background:%(surface)s;border:0;}
QProgressBar{background:%(line)s;border:0;border-radius:2px;max-height:5px;min-height:5px;}QProgressBar::chunk{background:%(blue)s;border-radius:2px;}QProgressBar[locked=true]{background:%(field)s;}
QScrollBar:vertical{background:transparent;width:6px;margin:2px 0;}QScrollBar::handle:vertical{background:%(line)s;border-radius:3px;min-height:24px;}QScrollBar::handle:vertical:hover{background:%(border_hover)s;}QScrollBar::add-line,QScrollBar::sub-line{height:0;width:0;}QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}
QScrollBar:horizontal{background:transparent;height:6px;margin:0 2px;}QScrollBar::handle:horizontal{background:%(line)s;border-radius:3px;min-width:24px;}QScrollBar::handle:horizontal:hover{background:%(border_hover)s;}
QToolTip{background:%(surface)s;color:%(text)s;border:1px solid %(line)s;padding:5px;}
QDialog{background:%(surface)s;color:%(text)s;}QMessageBox{background:%(surface)s;}
QFrame#calMetrics{background:%(surface)s;border:0;border-radius:4px;}QFrame#calMetrics QLabel{font-size:11px;color:%(muted)s;}QFrame#calMetrics QLabel[value=true]{font-family:Consolas;font-size:11px;color:%(text)s;}
QWidget#startupOverlay{background:rgba(6,11,20,110);}QFrame#startupCard{background:%(surface)s;border:1px solid %(line)s;border-radius:8px;}QLabel#startupTitle{font-size:17px;font-weight:600;}QLabel#startupMessage{color:%(muted)s;}
QLabel#toast{background:%(toast)s;border:1px solid %(border_hover)s;border-left:6px solid %(blue)s;border-radius:8px;padding:14px 22px;color:%(text)s;font-size:15px;font-weight:600;}
QLabel[error=true]{color:%(danger)s;}
QFrame#dialogDivider{background:%(line)s;border:0;}
QLabel#deviceState{font-size:12px;font-weight:600;padding:3px 10px;border-radius:10px;background:%(btn)s;color:%(muted)s;}
QLabel#deviceState[state=connected]{color:%(data)s;}QLabel#deviceState[state=simulated]{color:%(warning)s;}QLabel#deviceState[state=error]{color:%(danger)s;background:%(invalid)s;}
QLabel#infoKey,QLabel#fieldKey{color:%(muted)s;font-size:12px;}QLabel#infoValue{color:%(text)s;font-size:12px;}
QLabel#deviceMessage{color:%(muted)s;font-size:12px;}QLabel#deviceMessage[error=true]{color:%(danger)s;}QLabel#deviceNote{color:%(muted)s;font-size:11px;}
'''
VISUAL_QSS='''
QLabel{font-size:12px;}
QLabel[h2=true]{font-size:15px;font-weight:600;}
QLabel#brandTitle{font-size:19px;font-weight:600;}
QLabel#runStatus{font-size:12px;}
QLabel#progressText,QLabel#remaining,QLabel#sampleName{font-size:11px;}
QPushButton{font-size:13px;min-height:28px;border-radius:4px;}
QPushButton[nav=true]{font-size:12px;min-height:39px;padding:0 2px;}
QWidget#subnav{background:%(bg)s;}
QComboBox#sourceMode{min-height:27px;max-height:27px;font-size:11px;border-radius:4px;background:#e0a84a;color:#233249;}
QComboBox#sourceMode:hover,QComboBox#sourceMode:focus,QComboBox#sourceMode:on{background:#d3982f;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{min-height:30px;border-radius:4px;font-size:13px;}
QLineEdit[mono=true],QDoubleSpinBox,QSpinBox{font-family:Consolas;}
QLabel#unitSuffix{font-size:11px;}
QFrame#section{border-bottom:1px solid %(line)s;}
QFrame#sidebar QPushButton{font-size:12px;}
QPushButton#stageConnection,QPushButton#detectorConnection{min-height:24px;max-height:24px;font-size:11px;padding:0 7px;background:transparent;border:0;color:%(muted)s;}
QPushButton#stageConnection:hover,QPushButton#detectorConnection:hover{background:%(btn)s;color:%(text)s;}
QPushButton#stageConnection:disabled,QPushButton#detectorConnection:disabled{background:transparent;}
QPushButton#zero,QPushButton#returnZero{font-size:12px;font-weight:400;}
QPushButton#preview{font-size:11px;font-weight:400;min-height:25px;max-height:25px;padding:0 8px;}
QPushButton#addSegment{font-size:12px;min-height:26px;max-height:26px;}
QPushButton#summary{font-size:12px;border:0;border-radius:4px;min-height:32px;background:%(field)s;}QPushButton#summary:hover{background:%(field_hover)s;}
Segmented{background:%(track)s;border:0;border-radius:5px;}
Segmented QPushButton{font-size:12px;min-height:23px;max-height:25px;padding:0 8px;border:0;border-radius:4px;background:transparent;color:%(muted)s;}
Segmented QPushButton:hover,Segmented QPushButton:pressed{background:transparent;color:%(text)s;}
Segmented QPushButton[selected=true],Segmented QPushButton[selected=true]:hover{background:%(chip)s;color:%(chip_text)s;font-weight:600;}
Segmented QPushButton:disabled{background:transparent;color:%(border_hover)s;}
QWidget#panelHead QPushButton{font-size:12px;min-height:27px;max-height:27px;padding:0 8px;}
QWidget#panelHead Segmented QPushButton{min-height:23px;max-height:23px;padding:0 8px;}
QWidget#panelHead Legend QLabel{font-size:11px;}
QFrame#foot{background:%(surface)s;border:0;border-radius:7px;}
QFrame#foot QLabel,QFrame#foot QCheckBox{font-size:11px;}
QFrame#foot QPushButton,QFrame#foot QLineEdit{font-size:12px;}
QFrame#foot QCheckBox::indicator{width:12px;height:12px;}
QLabel#saveStatus{min-width:150px;max-width:280px;}
QFrame#analysisControls QLabel,QFrame#calibrationControls QLabel{font-size:13px;}
QFrame#analysisControls QLabel[h2=true],QFrame#calibrationControls QLabel[h2=true]{font-size:15px;}
QAbstractSpinBox QLineEdit{border:0;min-height:0;padding:0;background:transparent;border-radius:0;}
QTableWidget QDoubleSpinBox{min-height:26px;margin:4px 8px;}
QPushButton[nav=true]{min-height:37px;max-height:37px;border-bottom:2px solid transparent;}
QPushButton[nav=true][active=true]{border-bottom:2px solid %(blue)s;}
QPushButton#theme{min-width:27px;max-width:27px;min-height:27px;max-height:27px;padding:0;}
QHeaderView::section{font-size:12px;}
QTableWidget{font-size:13px;}
QLabel#advancedInfo{font-size:11px;color:%(muted)s;}QLabel#advancedHeading{font-size:11px;font-weight:600;color:%(text)s;padding-top:4px;border-top:1px solid %(line)s;}QLabel#coolingStatus{font-size:12px;color:%(text)s;}
QPushButton#plotChip{font-size:11px;min-height:22px;max-height:22px;padding:0 9px;border-radius:4px;background:%(btn)s;color:%(text)s;font-weight:400;}QPushButton#plotChip:hover{background:%(btn_hover)s;}
QLabel#plotReadout{font:11px Consolas;color:%(text)s;background:%(overlay)s;border-radius:4px;padding:3px 7px;}
QLineEdit#axisEndpointEdit{font:12px 'Segoe UI';min-height:0;padding:0 3px;border:0;border-bottom:1px solid %(blue)s;border-radius:0;background:%(surface)s;color:%(text)s;}
QLineEdit#axisEndpointEdit[invalid=true]{border-bottom:1px solid %(danger)s;color:%(danger)s;}
QFrame#segmentRow{background:%(field)s;border-radius:4px;}QFrame#segmentRow[invalid=true]{background:%(invalid)s;}
QFrame#segmentRow QLineEdit,QFrame#segmentRow QLineEdit:hover,QFrame#segmentRow QLineEdit:focus{background:transparent;border-radius:0;}
QFrame#segmentDivider{background:%(line)s;border:0;}
QPushButton#segmentRemove{padding:0;min-height:0;font-size:17px;background:transparent;border-radius:0;color:%(muted)s;}QPushButton#segmentRemove:hover{background:%(invalid)s;color:%(danger)s;}QPushButton#segmentRemove:disabled{background:transparent;}
QLabel#advancedCaption{font-size:11px;}
QLineEdit#inlineValue{font:10px 'Segoe UI';min-height:0;max-height:18px;padding:0 2px;border:0;border-bottom:1px dashed transparent;border-radius:0;background:transparent;color:%(text)s;}
QLineEdit#inlineValue:hover{border-bottom:1px dashed %(border_hover)s;color:%(blue)s;}QLineEdit#inlineValue:focus{background:%(surface)s;border-bottom:1px solid %(blue)s;color:%(text)s;}
QLineEdit#inlineValue[invalid=true]{border-bottom:1px solid %(danger)s;color:%(danger)s;}
QWidget#panelHead QPushButton#colorAuto{font-size:10px;min-height:18px;max-height:18px;padding:0 7px;border-radius:4px;background:%(btn)s;color:%(text)s;font-weight:400;}QWidget#panelHead QPushButton#colorAuto:hover{background:%(btn_hover)s;}
QLabel#analysisWindowLabel{font:12px Consolas;}
'''
def qss(mode=None):
    style=(QSS+VISUAL_QSS) % palette(mode)
    if (mode or _CURRENT)=='dark':style+='QComboBox#sourceMode{color:#0b1220;}'
    colors=palette(mode)
    def blend(fg,bg,amount=.4):
        a,b=QColor(fg),QColor(bg);return QColor(*[round(x*amount+y*(1-amount)) for x,y in zip(a.getRgb()[:3],b.getRgb()[:3])]).name()
    style+='QPushButton:disabled{background:'+blend(colors['btn'],colors['surface'],.55)+';color:'+blend(colors['text'],colors['surface'])+';}'
    style+='QPushButton[role=primary]:disabled{background:'+blend(colors['blue'],colors['surface'])+';color:'+blend('#ffffff',colors['surface'])+';}'
    for level,key in (('info','blue'),('success','success'),('warning','warning'),('error','danger')):
        style+=f"QLabel#toast[level={level}]{{border-color:{blend(colors[key],colors['toast'],.45)};border-left:6px solid {colors[key]};background:{blend(colors[key],colors['toast'],.12)};}}"
    style+='QPushButton[role=danger]:hover,QPushButton[role=danger]:pressed{background:'+blend(colors['danger'],colors['text'],.85)+';}'
    style+='QPushButton[role=danger]:disabled{background:'+blend(colors['danger'],colors['surface'])+';color:'+blend('#ffffff',colors['surface'])+';}'
    return style
def apply_theme(app,mode):
    global _CURRENT
    _CURRENT='dark' if mode=='dark' else 'light'
    app.setStyleSheet(qss(_CURRENT))
    colors=palette(_CURRENT); p=QPalette(); p.setColor(QPalette.Window,QColor(colors['bg'])); p.setColor(QPalette.Base,QColor(colors['field'])); p.setColor(QPalette.AlternateBase,QColor(colors['surface'])); p.setColor(QPalette.Text,QColor(colors['text'])); p.setColor(QPalette.WindowText,QColor(colors['text'])); p.setColor(QPalette.Button,QColor(colors['field'])); p.setColor(QPalette.ButtonText,QColor(colors['text'])); p.setColor(QPalette.Highlight,QColor(colors['blue'])); p.setColor(QPalette.HighlightedText,QColor(colors['bg'])); app.setPalette(p)
def apply_to_app(mode='light'):
    app=QApplication.instance()
    if app is not None: apply_theme(app,mode)
