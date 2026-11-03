'''Reusable native widgets, layout helpers and plot-adjacent presentation pieces.'''
import math
from PySide6.QtCore import Qt,Signal,QPoint,QTimer,QRectF,QObject,QEvent,QPointF,QSize
from PySide6.QtGui import QColor,QPainter,QPen,QLinearGradient,QFont,QPainterPath,QIcon,QPixmap,QPolygonF,QPalette
from PySide6.QtWidgets import QWidget,QLabel,QPushButton,QFrame,QVBoxLayout,QHBoxLayout,QGridLayout,QScrollArea,QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox,QCheckBox,QSizePolicy,QProgressBar,QToolButton,QAbstractSpinBox,QStylePainter,QStyleOptionComboBox,QStyle

def repolish(widget):
    try:
        widget.style().unpolish(widget); widget.style().polish(widget); widget.update()
    except Exception:
        pass
def set_prop(widget,name,value):
    widget.setProperty(name,value); repolish(widget)
def label(text, muted=False, mono=False, h2=False, warning=False):
    item=QLabel(text); item.setProperty('muted',bool(muted)); item.setProperty('mono',bool(mono)); item.setProperty('h2',bool(h2))
    if warning: item.setProperty('warning',True); item.setStyleSheet('color: palette(mid);')
    return item
def make_button(text, role=None, small=False, checkable=False):
    button=QPushButton(text); button.setCheckable(bool(checkable))
    if role: button.setProperty('role',role)
    if small: button.setProperty('small',True); button.setMinimumHeight(28)
    return button
def hbox(*widgets,spacing=8,margins=(0,0,0,0)):
    box=QHBoxLayout(); box.setContentsMargins(*margins); box.setSpacing(spacing)
    for widget in widgets:
        if widget is not None: box.addWidget(widget)
    return box
def vbox(*widgets,spacing=8,margins=(0,0,0,0)):
    box=QVBoxLayout(); box.setContentsMargins(*margins); box.setSpacing(spacing)
    for widget in widgets:
        if widget is not None: box.addWidget(widget)
    return box
class UnitField(QWidget):
    def __init__(self,control,unit,parent=None):
        super().__init__(parent); self.control=control; layout=QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0); layout.addWidget(control,1)
        self.unit=QLabel(unit,self);self.unit.setObjectName('unitSuffix');self.unit.setProperty('muted',True);self.unit.setAttribute(Qt.WA_TransparentForMouseEvents,True)
        if isinstance(control,QLineEdit):control.setTextMargins(0,0,max(18,self.unit.sizeHint().width())+10,0)
    def resizeEvent(self,event):
        super().resizeEvent(event);width=self.unit.sizeHint().width();self.unit.setGeometry(self.width()-width-8,0,width,self.height());self.unit.raise_()
class Field(QWidget):
    def __init__(self,title,control,unit=None,parent=None):
        super().__init__(parent); self.control=control; layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(4)
        self.label=QLabel(title); self.label.setProperty('muted',True); self.label.setProperty('mono',False); layout.addWidget(self.label)
        if not title:self.label.hide()
        layout.addWidget(UnitField(control,unit) if unit else control)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred)

class _SpinEditorGeometry:
    # Qt 6.12's stylesheet spin-box edit field can collapse to one pixel.
    # Keep the native editor, validator, keyboard and value signals intact.
    def _place_editor(self):
        edit=self.lineEdit()
        if edit is not None:edit.setGeometry(8,1,max(1,self.width()-16),max(1,self.height()-2))
    def resizeEvent(self,event):
        super().resizeEvent(event);self._place_editor()
    def showEvent(self,event):
        super().showEvent(event);self._place_editor()
class NativeSpinBox(_SpinEditorGeometry,QSpinBox):pass
class NativeDoubleSpinBox(_SpinEditorGeometry,QDoubleSpinBox):
    def textFromValue(self,value):
        text=super().textFromValue(value);decimal=self.locale().decimalPoint()
        return text.rstrip('0').rstrip(decimal) if decimal in text else text

class ArrowComboBox(QComboBox):
    def paintEvent(self,event):
        if self.property('centerText'):
            # Frame/background from the style, then the current text centred between equal side paddings.
            p=QStylePainter(self);option=QStyleOptionComboBox();self.initStyleOption(option);option.currentText=''
            p.drawComplexControl(QStyle.CC_ComboBox,option);p.setPen(self.palette().color(QPalette.Text));p.setFont(self.font())
            p.drawText(self.rect().adjusted(22,0,-22,0),Qt.AlignCenter,self.currentText());p.end()
        else:
            super().paintEvent(event)
        from .theme import palette
        # A combo with its own QSS text colour (e.g. the amber source selector) draws the arrow in that colour.
        color=self.palette().color(QPalette.Text) if self.property('ownTextColor') else QColor(palette()['text'] if self.isEnabled() else palette()['muted'])
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setPen(QPen(color,1.4));x=self.width()-12;y=self.height()/2;p.drawLine(int(x-3),int(y-1),int(x),int(y+2));p.drawLine(int(x),int(y+2),int(x+3),int(y-1))

class CheckBox(QCheckBox):
    def paintEvent(self,event):
        from .theme import palette
        colors=palette();p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);size=12 if self.objectName()=='autosave' else 13;y=(self.height()-size)/2
        left,top,right,bottom=pixel_edges(self,QRectF(0,round(y),size,size))
        p.setPen(crisp_pen(colors['blue'] if self.isChecked() else colors['border_hover']));p.setBrush(QColor(colors['blue'] if self.isChecked() else colors['surface']));p.drawRoundedRect(QRectF(left,top,right-left,bottom-top),2,2)
        if self.isChecked():
            pen=QPen(QColor('#ffffff'),1.5);pen.setCapStyle(Qt.RoundCap);pen.setJoinStyle(Qt.RoundJoin);p.setPen(pen);path=QPainterPath();path.moveTo(3,y+size*.5);path.lineTo(5,y+size*.7);path.lineTo(9,y+size*.3);p.drawPath(path)
        p.setFont(self.font());p.setPen(QColor(colors['muted']));p.drawText(QRectF(size+6,0,self.width()-size-6,self.height()),Qt.AlignVCenter|Qt.AlignLeft,self.text())

class WaveLogo(QWidget):
    def __init__(self):super().__init__();self.setFixedSize(17,24)
    def paintEvent(self,event):
        from .theme import palette
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setPen(QPen(QColor(palette()['text']),1.4));path=QPainterPath()
        for i in range(31):
            x=i/2;y=12-4*math.sin(x/15*math.pi*2)
            path.moveTo(x+1,y) if i==0 else path.lineTo(x+1,y)
        p.drawPath(path)
def field(title,control,unit=None):
    return Field(title,control,unit)
def number_edit(value='',minimum=-1e12,maximum=1e12,decimals=6):
    edit=QLineEdit(str(value)); edit.setAlignment(Qt.AlignLeft); edit.setProperty('mono',True)
    from PySide6.QtGui import QDoubleValidator
    validator=QDoubleValidator(minimum,maximum,decimals,edit); validator.setNotation(QDoubleValidator.StandardNotation); edit.setValidator(validator)
    return edit
def number_value(edit):
    text=edit.text().strip()
    if text=='': return None
    try: value=float(text)
    except Exception: return None
    return value if math.isfinite(value) else None
def spin(value=0.0,minimum=-1e12,maximum=1e12,decimals=4,step=0.1):
    box=NativeDoubleSpinBox(); box.setRange(minimum,maximum); box.setDecimals(decimals); box.setSingleStep(step); box.setValue(value); box.setButtonSymbols(QDoubleSpinBox.NoButtons); box.setKeyboardTracking(True)
    return box
def int_spin(value=0,minimum=0,maximum=1000000,step=1):
    box=NativeSpinBox(); box.setRange(minimum,maximum); box.setSingleStep(step); box.setValue(value); box.setButtonSymbols(QSpinBox.NoButtons)
    return box
class Segmented(QWidget):
    changed=Signal(str)
    def __init__(self,items=None,parent=None):
        super().__init__(parent); self.setObjectName('segmented');self.setAttribute(Qt.WA_StyledBackground,True);self.setFixedHeight(29); self.layout=QHBoxLayout(self); self.layout.setContentsMargins(2,2,2,2); self.layout.setSpacing(2)
        self.buttons=[]; self._value=''
        for text,value in (items or []): self.add(text,value,False)
    def add(self,text,value,selected=False):
        button=QPushButton(text);button.setFixedHeight(25); button.setCheckable(True); button.setProperty('segmentValue',value); button.clicked.connect(lambda checked=False,b=button,v=value:self.set_selected(v,emit=True))
        self.layout.addWidget(button,1); self.buttons.append(button)
        if selected or not self._value: self.set_selected(value,emit=False)
        return button
    def button(self,value):
        for button in self.buttons:
            if button.property('segmentValue')==value: return button
        return None
    def set_selected(self,value,emit=False):
        found=False
        for button in self.buttons:
            active=button.property('segmentValue')==value
            button.setChecked(active); set_prop(button,'selected',active)
            if active: found=True
        if found:
            self._value=str(value)
            if emit: self.changed.emit(self._value)
    def current(self):
        return self._value
    def set_enabled(self,value,enabled):
        button=self.button(value)
        if button: button.setEnabled(enabled)
class CollapsibleSection(QWidget):
    def __init__(self,title,parent=None):
        super().__init__(parent); self.layout=QVBoxLayout(self); self.layout.setContentsMargins(0,0,0,0); self.layout.setSpacing(0)
        self.button=QPushButton('▸ '+title); self.button.setObjectName('summary'); self.button.setCheckable(True); self.button.toggled.connect(self._toggle); self.layout.addWidget(self.button)
        self.content=QWidget(); self.content_layout=QVBoxLayout(self.content); self.content_layout.setContentsMargins(0,8,0,0); self.content.setVisible(False); self.layout.addWidget(self.content)
    def _toggle(self,open):
        self.button.setText(('▾ ' if open else '▸ ')+self.button.text()[2:]); self.content.setVisible(open)
    def set_open(self,open):
        self.button.setChecked(open); self.content.setVisible(open)
    def add(self,widget):
        self.content_layout.addWidget(widget)
def pixel_edges(widget,rect):
    """Edges of rect (widget coordinates) moved onto the centres of the device pixels just inside it.

    A one-device-pixel cosmetic pen drawn through these stays a single crisp pixel at any
    fractional Windows scale, where a plain 1 px QSS border is smeared over two pixels."""
    dpr=widget.devicePixelRatioF() or 1.0;origin=widget.mapTo(widget.window(),QPoint(0,0))
    def span(low,high,offset):
        first=math.ceil((offset+low)*dpr-1e-6);last=math.floor((offset+high)*dpr+1e-6)-1
        return (first+.5)/dpr-offset,(last+.5)/dpr-offset
    left,right=span(rect.left(),rect.right(),origin.x());top,bottom=span(rect.top(),rect.bottom(),origin.y())
    return left,top,right,bottom
def crisp_pen(color,width=1.0):
    pen=QPen(QColor(color));pen.setCosmetic(True);pen.setWidthF(width);return pen
class FocusRing(QWidget):
    """One crisp focus outline that follows the focused text field / combo box (inputs have no stroke of their own)."""
    TARGETS=(QLineEdit,QComboBox,QAbstractSpinBox)
    def __init__(self):
        super().__init__();self.setAttribute(Qt.WA_TransparentForMouseEvents,True);self.setAttribute(Qt.WA_NoSystemBackground,True);self.target=None;self.hide()
    def follow(self,old,new):
        widget=new
        if isinstance(widget,QLineEdit) and isinstance(widget.parentWidget(),QAbstractSpinBox):widget=widget.parentWidget()
        if widget is not None and (not isinstance(widget,self.TARGETS) or widget.objectName() in ('delay','axisEndpointEdit','inlineValue') or widget.window() is widget):widget=None
        if self.target is not None:
            try:self.target.removeEventFilter(self);self.target.destroyed.disconnect(self._target_destroyed)
            except (RuntimeError,TypeError):pass
        self.target=widget
        if widget is None:self.hide();self.setParent(None);return
        self.setParent(widget);widget.installEventFilter(self);widget.destroyed.connect(self._target_destroyed)
        self.setGeometry(widget.rect());self.show();self.raise_()
    def _target_destroyed(self,*args):
        # Detach before the target's destructor deletes its children (this ring included).
        self.target=None;self.hide();self.setParent(None)
    def eventFilter(self,obj,event):
        if obj is self.target and event.type() in (QEvent.Resize,QEvent.Move):self.setGeometry(obj.rect());self.update()
        return False
    def paintEvent(self,event):
        from .theme import palette
        target=self.target;radius=0 if target is not None and target.parentWidget() is not None and target.parentWidget().objectName()=='segmentRow' else 4
        left,top,right,bottom=pixel_edges(self,QRectF(self.rect()))
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setPen(crisp_pen(palette()['blue']));p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(left,top,right-left,bottom-top),radius,radius)
_FOCUS_RING=None
def install_focus_ring(app):
    global _FOCUS_RING
    if _FOCUS_RING is None:
        _FOCUS_RING=FocusRing();app.focusChanged.connect(_FOCUS_RING.follow)
    return _FOCUS_RING
def apply_pointer_cursors(root):
    """Clickable controls show a hand cursor; text fields keep the I-beam."""
    for widget in root.findChildren(QWidget):
        if isinstance(widget,(QPushButton,QComboBox,QCheckBox,QToolButton)):widget.setCursor(Qt.PointingHandCursor)
def parse_count(text):
    """'35.3k' / '2e4' / '1.2M' -> float; empty -> None; anything else raises ValueError."""
    text=str(text).strip().replace(',','')
    if not text:return None
    scale={'k':1e3,'m':1e6}.get(text[-1].lower(),1.0)
    value=float(text[:-1] if scale!=1.0 else text)*scale
    if not math.isfinite(value):raise ValueError('not finite')
    return value
class InlineValueEdit(QLineEdit):
    """A value that reads as plain text and becomes editable on click.

    Enter or focus-out emits committed(text); the owner answers accept() or reject().
    Esc (or a rejected focus-out) restores the shown value."""
    committed=Signal(str)
    def __init__(self,text='',parent=None):
        super().__init__(text,parent);self.setObjectName('inlineValue');self.setFrame(False);self.setAlignment(Qt.AlignCenter);self.setFixedHeight(18)
        self._shown=str(text);self.textChanged.connect(self._fit);self._fit()
    def set_value_text(self,text):
        self._shown=str(text)
        if not self.hasFocus():self.setText(self._shown)
    def _fit(self,*args):
        self.setFixedWidth(max(26,self.fontMetrics().horizontalAdvance(self.text() or ' ')+10))
    def focusInEvent(self,event):
        super().focusInEvent(event);QTimer.singleShot(0,self.selectAll)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape:
            self._revert();self.clearFocus();return
        if event.key() in (Qt.Key_Return,Qt.Key_Enter):
            self._commit();return
        super().keyPressEvent(event)
    def focusOutEvent(self,event):
        super().focusOutEvent(event);self._commit()
    def _commit(self):
        if self.text().strip()==self._shown:
            self._revert();self.clearFocus();return
        self.committed.emit(self.text())
    def _revert(self):
        set_prop(self,'invalid',False);self.setText(self._shown)
    def accept(self):
        self._shown=self.text().strip();set_prop(self,'invalid',False);self.clearFocus()
    def reject(self):
        if self.hasFocus():set_prop(self,'invalid',True)
        else:self._revert()
class ElidedLabel(QLabel):
    """Single-line label that elides with … and keeps the full text in its tooltip."""
    def __init__(self,text='',parent=None):
        super().__init__(parent);self._full='';self.setText(text)
    def setText(self,text):
        self._full=str(text);self.setToolTip(self._full);super().setText(self._full);self.update()
    def text(self):
        return self._full
    def minimumSizeHint(self):
        hint=super().minimumSizeHint();return QSize(min(hint.width(),self.minimumWidth() or 40),hint.height())
    def paintEvent(self,event):
        p=QPainter(self);p.setPen(self.palette().color(self.foregroundRole()));p.setFont(self.font())
        p.drawText(self.contentsRect(),Qt.AlignVCenter|Qt.AlignLeft,self.fontMetrics().elidedText(self._full,Qt.ElideMiddle,self.contentsRect().width()))
class PanelOutline(QWidget):
    """Draw the shared rounded edge above flush chart children.

    The edge is a one-device-pixel cosmetic line centred on whole device pixels, so
    fractional Windows scaling (125 %, 150 %) does not smear it into a soft two-pixel band."""
    def __init__(self,parent):
        super().__init__(parent);self.setAttribute(Qt.WA_TransparentForMouseEvents,True)
    def paintEvent(self,event):
        from .theme import palette
        left,top,right,bottom=pixel_edges(self,QRectF(self.rect()));colors=palette()
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        # Flush children (plots, tables) paint square; cover their corners with the page background.
        outside=QPainterPath();outside.addRect(QRectF(self.rect()));inside=QPainterPath();inside.addRoundedRect(QRectF(left,top,right-left,bottom-top),7,7)
        p.fillPath(outside.subtracted(inside),QColor(colors['bg']))
        p.setPen(crisp_pen(colors['line']));p.setBrush(Qt.NoBrush);p.drawRoundedRect(QRectF(left,top,right-left,bottom-top),7,7)
class _OutlineFollower(QObject):
    def __init__(self,frame):
        super().__init__(frame);self.outline=PanelOutline(frame);frame.installEventFilter(self)
    def eventFilter(self,obj,event):
        if event.type() in (QEvent.Resize,QEvent.Show,QEvent.Move,QEvent.ChildAdded):
            self.outline.setGeometry(obj.rect());self.outline.raise_()
        return False
def add_outline(frame):
    """Give a card frame the crisp painted edge used by Panel (its QSS border stays 0)."""
    return _OutlineFollower(frame).outline

_RUN_ICONS={}
def run_icon(kind,color,size=12):
    """Painted play / pause / stop glyph; font glyphs such as ❚❚ render unevenly in Segoe UI."""
    key=(kind,color,size)
    if key in _RUN_ICONS:return _RUN_ICONS[key]
    scale=4;pixmap=QPixmap(size*scale,size*scale);pixmap.fill(Qt.transparent);pixmap.setDevicePixelRatio(scale)
    p=QPainter(pixmap);p.setRenderHint(QPainter.Antialiasing);p.setPen(Qt.NoPen);p.setBrush(QColor(color));s=float(size)
    if kind=='play':p.drawPolygon(QPolygonF([QPointF(s*.2,s*.1),QPointF(s*.9,s*.5),QPointF(s*.2,s*.9)]))
    elif kind=='pause':p.drawRoundedRect(QRectF(s*.18,s*.12,s*.22,s*.76),1,1);p.drawRoundedRect(QRectF(s*.6,s*.12,s*.22,s*.76),1,1)
    else:p.drawRoundedRect(QRectF(s*.15,s*.15,s*.7,s*.7),1.5,1.5)
    p.end();_RUN_ICONS[key]=QIcon(pixmap);return _RUN_ICONS[key]
class ThemeButton(QPushButton):
    def __init__(self):
        super().__init__('');self.setAccessibleName('Toggle light or dark theme');self.setToolTip('Toggle light or dark theme')
    def paintEvent(self,event):
        super().paintEvent(event)
        from .theme import palette
        # Drawn in device pixels: the half-disc is an outline-free fill cut on a pixel boundary,
        # and the ring stroke is a whole number of device pixels, so nothing smears at 125 % / 150 %.
        color=QColor(palette()['text']);dpr=self.devicePixelRatioF()
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.scale(1/dpr,1/dpr)
        cx=round(self.width()*dpr/2);cy=round(self.height()*dpr/2);stroke=max(1,round(1.3*dpr));radius=round(13*dpr)/2-stroke/2
        disc=QPainterPath();disc.addEllipse(QPointF(cx,cy),radius,radius)
        half=QPainterPath();half.addRect(QRectF(cx-radius-stroke,cy-radius-stroke,radius+stroke,2*radius+2*stroke))
        p.setPen(Qt.NoPen);p.setBrush(color);p.drawPath(disc.intersected(half))
        p.setBrush(Qt.NoBrush);p.setPen(QPen(color,stroke));p.drawEllipse(QPointF(cx,cy),radius,radius)

class Panel(QFrame):
    def __init__(self,title,parent=None):
        super().__init__(parent); self.setObjectName('panel'); self.layout=QVBoxLayout(self); self.layout.setContentsMargins(0,0,0,0); self.layout.setSpacing(0)
        self.header=QWidget(); self.header.setObjectName('panelHead');self.header.setFixedHeight(49); self.header_layout=QHBoxLayout(self.header); self.header_layout.setContentsMargins(14,0,14,0); self.header_layout.setSpacing(10)
        self.title_label=QLabel(title); self.title_label.setProperty('h2',True); self.title_layout=QHBoxLayout(); self.title_layout.setContentsMargins(0,0,0,0); self.title_layout.setSpacing(10); self.title_layout.addWidget(self.title_label); self.header_layout.addLayout(self.title_layout); self.header_layout.addStretch(1)
        self.tools=QWidget(); self.tools_layout=QHBoxLayout(self.tools); self.tools_layout.setContentsMargins(0,0,0,0); self.tools_layout.setSpacing(10); self.header_layout.addWidget(self.tools)
        self.body=QWidget(); self.body.setObjectName('panelBody'); self.body_layout=QVBoxLayout(self.body); self.body_layout.setContentsMargins(0,0,0,0); self.body_layout.setSpacing(0)
        self.layout.addWidget(self.header); self.layout.addWidget(self.body,1);self.outline=PanelOutline(self)
    def resizeEvent(self,event):
        super().resizeEvent(event);self.outline.setGeometry(self.rect());self.outline.raise_()
    def moveEvent(self,event):
        super().moveEvent(event);self.outline.update()
    def showEvent(self,event):
        super().showEvent(event);self.outline.setGeometry(self.rect());self.outline.raise_()
    def add_tool(self,widget):
        self.tools_layout.addWidget(widget)
    def add_body(self,widget,stretch=0):
        self.body_layout.addWidget(widget,stretch)
class Swatch(QWidget):
    def __init__(self,dashed=False,dot=False,color=None,parent=None):
        super().__init__(parent); self.dashed=dashed; self.dot=dot; self.color=color; self.setFixedSize(16,10)
    def paintEvent(self,event):
        from .theme import palette
        colors=palette(); painter=QPainter(self); color=QColor(self.color or (colors['amber'] if self.dashed else colors['data'])); pen=QPen(color,2); pen.setStyle(Qt.DashLine if self.dashed else Qt.SolidLine); painter.setPen(pen); y=5
        if self.dot: painter.setBrush(color); painter.drawEllipse(5,1,7,7)
        else: painter.drawLine(0,y,15,y)
class Legend(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self.layout=QHBoxLayout(self); self.layout.setContentsMargins(0,0,0,0); self.layout.setSpacing(10)
    def add(self,text,dashed=False,dot=False,color=None):
        box=QWidget(); row=QHBoxLayout(box); row.setContentsMargins(0,0,0,0); row.setSpacing(5); row.addWidget(Swatch(dashed,dot,color)); item=QLabel(text); item.setProperty('muted',True)
        item.setSizePolicy(QSizePolicy.Fixed,QSizePolicy.Preferred)  # never clipped when a neighbouring entry's text grows
        row.addWidget(item); self.layout.addWidget(box); return box
class GradientBar(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self.setMinimumWidth(85); self.setFixedHeight(7); self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
    def paintEvent(self,event):
        from .theme import map_qcolors
        colors=map_qcolors(); gradient=QLinearGradient(0,0,self.width(),0)
        for index,color in enumerate(colors): gradient.setColorAt(index/(len(colors)-1),color)
        painter=QPainter(self); painter.setRenderHint(QPainter.Antialiasing); painter.fillRect(self.rect(),gradient)
class StatusLabel(QWidget):
    def __init__(self,text='',parent=None):
        super().__init__(parent); self.layout=QHBoxLayout(self); self.layout.setContentsMargins(0,0,0,0); self.layout.setSpacing(5)
        self.dot=QLabel('●'); self.dot.setObjectName('statusDot'); self.text_label=QLabel(text); self.layout.addWidget(self.dot); self.layout.addWidget(self.text_label)
        self.set_state('success')
    def set_state(self,state):
        set_prop(self,'state',state); set_prop(self.dot,'state',state)
    def setText(self,text):
        self.text_label.setText(text)
    def text(self):
        return self.text_label.text()
class BusySpinner(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.setFixedSize(24,24);self.angle=0
        self.timer=QTimer(self);self.timer.setInterval(40);self.timer.timeout.connect(self.advance)
    def advance(self):
        self.angle=(self.angle+16)%360;self.update()
    def showEvent(self,event):
        self.timer.start();super().showEvent(event)
    def hideEvent(self,event):
        self.timer.stop();super().hideEvent(event)
    def paintEvent(self,event):
        from .theme import palette
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);rect=QRectF(3,3,18,18)
        p.setPen(QPen(QColor(palette()['line']),3));p.drawEllipse(rect)
        p.setPen(QPen(QColor(palette()['blue']),3));p.drawArc(rect,-self.angle*16,100*16)

class StartupOverlay(QFrame):
    def __init__(self,parent=None):
        super().__init__(parent); self.setObjectName('startupOverlay'); self.setAttribute(Qt.WA_StyledBackground,True)
        outer=QVBoxLayout(self); outer.setContentsMargins(16,16,16,16); outer.addStretch(1)
        card=QFrame(); card.setObjectName('startupCard'); card.setMinimumWidth(390); card_layout=QVBoxLayout(card); card_layout.setContentsMargins(24,22,24,22); card_layout.setSpacing(10)
        row=QHBoxLayout(); self.spinner=BusySpinner(); row.addWidget(self.spinner); title=QLabel('Starting local service…'); title.setObjectName('startupTitle'); row.addWidget(title); row.addStretch(1); card_layout.addLayout(row)
        self.message=QLabel('Preparing the acquisition service. Your workbench will be ready shortly.'); self.message.setObjectName('startupMessage'); self.message.setWordWrap(True); card_layout.addWidget(self.message)
        self.retry=QPushButton('Retry connection'); self.retry.setObjectName('startupRetry'); self.retry.hide(); card_layout.addWidget(self.retry,0,Qt.AlignLeft)
        outer.addWidget(card,0,Qt.AlignCenter); outer.addStretch(1)
    def show_message(self,text,error=False):
        self.message.setText(text); self.retry.setVisible(error); self.spinner.setVisible(not error); self.show(); self.raise_()
    def set_ready(self):
        self.hide()
    def resize_to(self,width,height):
        self.setGeometry(0,0,width,height)
class MetricsOverlay(QFrame):
    def __init__(self,parent=None):
        super().__init__(parent); self.setObjectName('calMetrics'); self.setToolTip('Drag to reposition'); self.setCursor(Qt.OpenHandCursor)
        layout=QHBoxLayout(self); layout.setContentsMargins(7,5,7,5); layout.setSpacing(10); self.values={}
        for key,title in [('rms','RMS'),('max','Max |Δ|'),('range','Line coverage')]:
            box=QWidget(); column=QVBoxLayout(box); column.setContentsMargins(0,0,0,0); column.setSpacing(0); caption=QLabel(title); caption.setProperty('muted',True); value=QLabel('—'); value.setProperty('value',True); value.setObjectName('calRms' if key=='rms' else 'calMaxError' if key=='max' else 'calRange'); column.addWidget(caption); column.addWidget(value); layout.addWidget(box); self.values[key]=value
        self._drag=None; self._moved=False
    def set_values(self,rms=None,max_error=None,coverage=None):
        self.values['rms'].setText(rms or '—'); self.values['max'].setText(max_error or '—'); self.values['range'].setText(coverage or '—')
        for child in self.findChildren(QWidget,options=Qt.FindDirectChildrenOnly):
            if child.layout() is not None:child.layout().invalidate();child.layout().activate()
        self.layout().invalidate();self.layout().activate();self.setFixedSize(self.layout().sizeHint());self.place()
    def place(self):
        parent=self.parentWidget()
        if parent is not None and not self._moved: self.move(max(8,parent.width()-self.sizeHint().width()-24),16)
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            self._drag=event.globalPosition().toPoint()-self.pos(); self.setCursor(Qt.ClosedHandCursor)
    def mouseMoveEvent(self,event):
        if self._drag is not None and self.parentWidget() is not None:
            point=event.globalPosition().toPoint()-self._drag; parent=self.parentWidget(); x=max(0,min(parent.width()-self.width(),point.x())); y=max(0,min(parent.height()-self.height(),point.y())); self.move(x,y); self._moved=True; self.setCursor(Qt.ClosedHandCursor)
    def mouseReleaseEvent(self,event):
        self._drag=None; self.setCursor(Qt.OpenHandCursor)
def fmt_value(value,digits=4):
    if value is None: return '—'
    try: value=float(value)
    except Exception: return '—'
    if not math.isfinite(value): return '—'
    if value!=0 and (abs(value)>=100000 or abs(value)<0.001): return f'{value:.2e}'
    text=f'{value:.{digits}g}'
    return text
class StatusButton(QPushButton):
    '''Heading button that draws one status dot per device: off, connecting (blinking), connected, error or simulated.'''
    DOT={'connected':'success','connecting':'warning','simulated':'warning','error':'danger','off':'muted','manual':'muted'}
    def __init__(self,parent=None):
        super().__init__('',parent); self._parts=[]; self._blink_on=True
        self._blink=QTimer(self); self._blink.setInterval(450); self._blink.timeout.connect(self._toggle_blink)
    def set_parts(self,parts):
        parts=[(str(state),str(text)) for state,text in parts]
        if parts==self._parts: return
        self._parts=parts; self.setAccessibleName(' · '.join(f'{text} {state}' for state,text in parts))
        if any(state=='connecting' for state,_ in parts):
            if not self._blink.isActive(): self._blink_on=True; self._blink.start()
        else: self._blink.stop(); self._blink_on=True
        self.updateGeometry(); self.update()
    def parts(self): return list(self._parts)
    def _toggle_blink(self): self._blink_on=not self._blink_on; self.update()
    def _layout(self):
        metrics=self.fontMetrics(); dot=7; gap=5; between=10; items=[]; x=0
        for state,text in self._parts:
            items.append((x,state,text)); x+=dot+gap+metrics.horizontalAdvance(text)+between
        return items,max(0,x-between)+metrics.horizontalAdvance(' ▾'),dot,gap
    def sizeHint(self):
        _,width,_,_=self._layout(); base=super().sizeHint()
        return QSize(width+16,base.height())
    def minimumSizeHint(self): return self.sizeHint()
    def paintEvent(self,event):
        super().paintEvent(event)
        from .theme import palette
        colors=palette(); items,width,dot,gap=self._layout()
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setFont(self.font()); metrics=self.fontMetrics()
        left=(self.width()-width)/2; middle=self.height()/2; baseline=middle+(metrics.ascent()-metrics.descent())/2
        text_color=QColor(colors['text'] if self.underMouse() and self.isEnabled() else colors['muted'])
        for x,state,text in items:
            color=QColor(colors[self.DOT.get(state,'muted')])
            if state=='connecting' and not self._blink_on: color.setAlphaF(.25)
            rect=QRectF(left+x+.5,middle-dot/2,dot-1,dot-1)
            if state=='off': p.setPen(QPen(color,1.2)); p.setBrush(Qt.NoBrush)
            else: p.setPen(Qt.NoPen); p.setBrush(color)
            p.drawEllipse(rect)
            p.setPen(text_color); p.drawText(QPointF(left+x+dot+gap,baseline),text)
        p.setPen(text_color); p.drawText(QPointF(left+width-metrics.horizontalAdvance(' ▾'),baseline),' ▾')
        p.end()
def device_availability(snapshot,online):
    available=(snapshot or {}).get('available') or {}
    return {'stage':bool(online and available.get('stage')),'camera':bool(online and available.get('camera')),'spectrometer':bool(online and available.get('spectrometer')),'spectrograph':bool(online and available.get('spectrograph_config')),'spectralControls':list(available.get('spectrograph_controls') or []) if online else [],'scan':bool(online and available.get('scan')),'preview':bool(online and available.get('preview')),'background':bool(online and available.get('background')),'calibration':bool(online and available.get('calibration_capture'))}
def clear_layout(layout):
    while layout.count():
        item=layout.takeAt(0); widget=item.widget()
        if widget is not None: widget.setParent(None); widget.deleteLater()
        elif item.layout() is not None: clear_layout(item.layout())
