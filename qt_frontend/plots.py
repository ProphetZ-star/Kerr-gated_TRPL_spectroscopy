'''Native pyqtgraph plots with inline editable axis endpoints.'''
import math
import time
import numpy as np
import pyqtgraph as pg
import pyqtgraph.functions as fn
from PySide6.QtCore import Qt, Signal, QEvent, QPointF, QRectF, QRect
from PySide6.QtGui import QColor,QFont,QFontMetricsF,QPen,QPainterPath
from PySide6.QtWidgets import QFrame, QLineEdit, QHBoxLayout, QGraphicsRectItem, QGraphicsPathItem, QGraphicsSimpleTextItem, QGraphicsItem, QWidget, QPushButton, QLabel
from .theme import palette, map_color, map_qcolors, SATURATED_COLOR

def saturated_rgb():
    color = QColor(SATURATED_COLOR)
    return np.array([color.red(), color.green(), color.blue()], dtype=np.uint8)

# left axis width, right / top gutter, bottom axis height. The right gutter matches the 14 px panel header padding.
PLOT_MARGINS=(68,14,10,46)

def fmt(value, digits=4):
    try:
        value = float(value)
    except Exception:
        return '-'
    if not math.isfinite(value):
        return '-'
    if value != 0 and (abs(value) >= 100000 or abs(value) < 0.0001):
        return f'{value:.3e}'
    return f'{value:.{digits}g}'

def nice_step(span, target, integer=False):
    '''Tick spacing from the 1 / 2 / 2.5 / 5 x 10^n series for about `target` intervals over span.'''
    raw = span / max(1, target)
    if not math.isfinite(raw) or raw <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(raw))
    candidates = [factor * magnitude for factor in (1, 2, 2.5, 5, 10)]
    if integer:
        candidates = [value for value in candidates if value >= 1 - 1e-9 and abs(value - round(value)) < 1e-6] or [1.0]
        candidates = [float(round(value)) for value in candidates]
    # Largest round step not much coarser than requested, so short axes still get 3-5 labelled ticks.
    fitting = [value for value in candidates if value <= raw * 1.25]
    return max(fitting) if fitting else min(candidates)

def padded_range(values, low_pad=.05, high_pad=.1):
    '''Automatic axis range from the data at both ends (no end pinned to 0): a margin below the lowest and above
    the highest value, as fractions of the span.'''
    values = np.asarray([v for v in values if v is not None], dtype=float)
    values = values[np.isfinite(values)]
    if not values.size:
        return 0.0, 1.0
    low, high = float(values.min()), float(values.max())
    span = high - low if high > low else (abs(high) * .1 or 1.0)
    # The margin never takes an all-positive range (counts) below zero.
    bottom = low - span * low_pad
    return (0.0 if low >= 0 > bottom else bottom), high + span * high_pad

def _decimals(step):
    for digits in range(7):
        scaled = step * 10 ** digits
        if abs(scaled - round(scaled)) < 1e-6 * max(1.0, abs(scaled)):
            return digits
    return 6

def format_tick(value, step, extra=0):
    '''Tick text with just enough decimals for the step; steps of 1000 and more use a k suffix.'''
    if abs(value) < abs(step) * 1e-6:
        return '0'
    suffix = ''
    if abs(step) >= 1000:
        value, step, suffix = value / 1000, step / 1000, 'k'
    text = f'{value:.{_decimals(step) + extra}f}'
    if extra and '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text + suffix

def format_count(value, significant=3):
    '''Counts without exponent notation: 1, 10, 100, 1k, 10k, 1.2M.'''
    for divisor, suffix in ((1e6, 'M'), (1e3, 'k'), (1.0, '')):
        if abs(value) >= divisor or divisor == 1.0:
            scaled = value / divisor
            break
    digits = max(0, significant - 1 - math.floor(math.log10(abs(scaled)))) if scaled else 0
    text = f'{scaled:.{digits}f}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text + suffix

def is_pixel_label(label):
    label = (label or '').strip().lower()
    if ' / ' in label:
        name, unit = label.split(' / ', 1)
        return unit.strip() in ('px', 'pixel', 'pixels') and not name.strip().startswith('count')
    return label in ('pixel', 'pixels', 'ccd pixel', 'ccd row', 'ccd column', 'row', 'column')

def format_duration(seconds):
    '''Seconds below 1.5 min, then minutes, then hours: "48 s", "12 min 05 s", "2 h 07 min".'''
    seconds = max(0.0, float(seconds))
    if seconds < 10:
        return f'{seconds:.1f} s'
    if seconds < 90:
        return f'{seconds:.0f} s'
    total = int(round(seconds))
    if total < 3600:
        minutes, rest = divmod(total, 60)
        return f'{minutes} min {rest:02d} s'
    hours, rest = divmod(total, 3600)
    return f'{hours} h {round(rest / 60):02d} min'

def _polyval(coeffs, x):
    values = np.zeros_like(np.asarray(x, dtype=float))
    for coefficient in reversed(list(coeffs)):
        values = values * x + float(coefficient)
    return values

def set_invalid(widget, value):
    widget.setProperty('invalid', bool(value))
    widget.style().unpolish(widget); widget.style().polish(widget)

def label_parts(label):
    '''(prefix, unit) for readouts from an axis title: "Delay / ps" -> ("", "ps"), "counts / s" -> ("", "counts/s"),
    "Residual" -> ("Residual", "").'''
    label = (label or '').strip()
    if ' / ' in label:
        name, unit = label.split(' / ', 1)
        return '', label.replace(' ', '') if name.strip().lower().startswith('count') else unit.strip()
    return label, ''

def format_reading(value, label):
    prefix, unit = label_parts(label)
    return f'{prefix} {fmt(value, 5)} {unit}'.strip()

def pick_reading(value, label):
    '''One coordinate of a picked point, keeping the axis name: "Delay / ps" -> "Delay 1.25 ps".'''
    label = (label or '').strip()
    name, unit = label.split(' / ', 1) if ' / ' in label else (label, '')
    if name.strip().lower().startswith('count'):
        return f'{fmt(value, 5)} {label.replace(" ", "")}'
    return f'{name.strip()} {fmt(value, 5)} {unit.strip()}'.strip()

class ZoomGroup:
    '''Shared zoom history for plots whose axes are linked or synchronised.

    Every entry is a snapshot of each member's manual (user-chosen) axis ranges taken before a
    zoom, pan, reset or limit edit, so one undo restores all linked views together.'''
    LIMIT = 40
    def __init__(self, plot):
        self.members = [plot]
        self.stack = []
    def merge(self, other):
        if other is self:
            return
        for plot in other.members:
            plot.zoom_group = self
            if plot not in self.members:
                self.members.append(plot)
        self.stack = []
    def push(self):
        snapshot = [(plot, dict(plot._manual_ranges)) for plot in self.members]
        if self.stack and all(a[1] == b[1] for a, b in zip(self.stack[-1], snapshot)):
            return
        self.stack.append(snapshot)
        del self.stack[:-self.LIMIT]
    def undo(self):
        if not self.stack:
            return False
        for plot, manual in self.stack.pop():
            plot._restore_manual(manual)
        self.changed()
        return True
    def changed(self):
        for plot in self.members:
            plot._refresh_zoom_bar()

class ZoomViewBox(pg.ViewBox):
    '''Left-drag draws a zoom box (Shift: x only, Alt: y only); right / middle drag pans;
    the wheel zooms in finer steps (Ctrl: finer still), on an axis only that axis.'''
    WHEEL_STEP = 1.15
    FINE_STEP = 1.04
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = None
        self._box = None
        self._box_label = None
        self._last_wheel = 0.0
        self.setCursor(Qt.CrossCursor)
    def _ensure_box(self):
        if self._box is None:
            self._box = QGraphicsRectItem(self)
            self._box.setZValue(1e8)
            self._box.setAcceptedMouseButtons(Qt.NoButton)
            self._box_label = QGraphicsRectItem(self)
            self._box_label.setZValue(1e8 + 1)
            self._box_label.setAcceptedMouseButtons(Qt.NoButton)
            self._box_text = QGraphicsSimpleTextItem(self._box_label)
            font = QFont('Consolas'); font.setPixelSize(11); self._box_text.setFont(font)
        colors = palette()
        pen = pg.mkPen(colors['blue']); pen.setCosmetic(True)
        fill = QColor(colors['blue']); fill.setAlpha(36)
        self._box.setPen(pen); self._box.setBrush(fill)
        back = QColor(colors['surface']); back.setAlpha(230)
        self._box_label.setPen(pg.mkPen(None)); self._box_label.setBrush(back)
        self._box_text.setBrush(QColor(colors['text']))
    def _hide_box(self):
        if self._box is not None:
            self._box.hide(); self._box_label.hide()
    def mouseDragEvent(self, ev, axis=None):
        owner = self.owner
        if owner is None:
            return super().mouseDragEvent(ev, axis)
        button = ev.button()
        if axis is not None or button in (Qt.RightButton, Qt.MiddleButton):
            ev.accept()
            if ev.isStart():
                owner.zoom_group.push()
                self.setCursor(Qt.ClosedHandCursor)
            mask = [1, 1]
            if axis in (0, 1):
                mask[1 - axis] = 0
            dif = (ev.pos() - ev.lastPos()) * -1
            transform = fn.invertQTransform(self.childGroup.transform())
            shift = transform.map(QPointF(dif.x() * mask[0], dif.y() * mask[1])) - transform.map(QPointF(0, 0))
            self._resetTarget()
            self.translateBy(x=shift.x() if mask[0] else None, y=shift.y() if mask[1] else None)
            owner._commit_view([name for name, used in zip('xy', mask) if used])
            if ev.isFinish():
                self.setCursor(Qt.CrossCursor)
            return
        if button != Qt.LeftButton:
            ev.ignore()
            return
        ev.accept()
        modifiers = ev.modifiers()
        mode = 'x' if modifiers & Qt.ShiftModifier else 'y' if modifiers & Qt.AltModifier else 'xy'
        bounds = self.rect()
        start = ev.buttonDownPos(Qt.LeftButton); current = ev.pos()
        x0, x1 = sorted((start.x(), current.x())); y0, y1 = sorted((start.y(), current.y()))
        x0, x1 = max(bounds.left(), x0), min(bounds.right(), x1)
        y0, y1 = max(bounds.top(), y0), min(bounds.bottom(), y1)
        if mode == 'x':
            y0, y1 = bounds.top(), bounds.bottom()
        elif mode == 'y':
            x0, x1 = bounds.left(), bounds.right()
        first = self.mapToView(QPointF(x0, y0)); second = self.mapToView(QPointF(x1, y1))
        x_range = tuple(sorted((first.x(), second.x()))); y_range = tuple(sorted((first.y(), second.y())))
        if ev.isFinish():
            self._hide_box()
            if (mode != 'y' and x1 - x0 < 4) or (mode != 'x' and y1 - y0 < 4):
                return
            owner.apply_user_range(x=x_range if mode != 'y' else None, y=y_range if mode != 'x' else None)
            return
        self._ensure_box()
        self._box.setRect(QRectF(x0, y0, x1 - x0, y1 - y0)); self._box.show()
        self._box_text.setText(owner.range_text(x_range if mode != 'y' else None, y_range if mode != 'x' else None))
        text = self._box_text.boundingRect()
        label = QRectF(0, 0, text.width() + 12, text.height() + 6)
        lx = min(max(bounds.left() + 4, x1 - label.width()), bounds.right() - label.width() - 4)
        ly = y1 + 6 if y1 + 6 + label.height() < bounds.bottom() - 4 else max(bounds.top() + 4, y0 - label.height() - 6)
        self._box_label.setRect(label); self._box_label.setPos(lx, ly); self._box_text.setPos(6, 3); self._box_label.show()
    def wheelEvent(self, ev, axis=None):
        owner = self.owner
        if owner is None:
            return super().wheelEvent(ev, axis)
        mask = [True, True]
        if axis in (0, 1):
            mask[1 - axis] = False
        step = self.FINE_STEP if ev.modifiers() & Qt.ControlModifier else self.WHEEL_STEP
        scale = step ** (-ev.delta() / 120.0)
        center = fn.invertQTransform(self.childGroup.transform()).map(ev.pos())
        now = time.monotonic()
        if now - self._last_wheel > 0.8:
            owner.zoom_group.push()
        self._last_wheel = now
        self._resetTarget()
        self.scaleBy(x=scale if mask[0] else None, y=scale if mask[1] else None, center=center)
        owner._commit_view([name for name, used in zip('xy', mask) if used])
        ev.accept()
    def mouseClickEvent(self, ev):
        if self.owner is not None and ev.button() == Qt.LeftButton and ev.double():
            ev.accept()
            self.owner.reset_view()
            return
        if self.owner is not None and ev.button() == Qt.LeftButton and getattr(self.owner, '_pickables', None):
            self.owner.pick_at(ev.scenePos())
        super().mouseClickEvent(ev)

AXIS_TEXT_OFFSET=4
AXIS_TITLE_GAP=4
class InlineAxis(pg.AxisItem):
    def __init__(self, orientation, owner=None):
        super().__init__(orientation=orientation)
        self.owner = owner
        self._hovered = None
    def _end_font(self):
        font = QFont('Segoe UI'); font.setPixelSize(12)
        return font
    def endpoint_rects(self):
        '''Where drawPicture puts the min / max labels, in axis coordinates.'''
        metrics = QFontMetricsF(self._end_font())
        ends = self.endpoint_strings()
        out = {}
        for index, text in enumerate(ends[:2]):
            width = metrics.horizontalAdvance(text)
            if self.orientation == 'bottom':
                x = self.geometry().width() - width if index else 0
                baseline = 20
            else:
                top = (index == 0) if self.owner._invert_y else (index == 1)
                x = self.geometry().width() - AXIS_TEXT_OFFSET - width
                baseline = metrics.ascent() - 1 if top else self.geometry().height() - 1
            out['min' if index == 0 else 'max'] = QRectF(x, baseline - metrics.ascent(), width, metrics.height())
        return out
    def endpoint_at(self, pos):
        if self.owner is None or not self.style['showValues']:
            return None
        for endpoint, rect in self.endpoint_rects().items():
            if rect.adjusted(-6, -4, 6, 4).contains(pos):
                return endpoint
        return None
    def hoverEvent(self, ev):
        hovered = None if ev.isExit() else self.endpoint_at(ev.pos())
        if hovered != self._hovered:
            self._hovered = hovered
            if hovered:
                self.setCursor(Qt.PointingHandCursor)
                self.setToolTip('Click to type this axis limit')
            else:
                self.unsetCursor()
                self.setToolTip('')
            self.picture = None
            self.update()
    def integer_axis(self):
        '''Pixel / row axes only ever show whole numbers.'''
        forced = getattr(self, 'integer_ticks', None)
        return bool(forced) if forced is not None else is_pixel_label(self.labelText)
    def _step(self):
        low, high = sorted(self.range)
        if self.logMode:
            return max(1.0, nice_step(high - low, getattr(self, 'divisions', 4), integer=True))
        return nice_step(high - low, getattr(self, 'divisions', 4), integer=self.integer_axis())
    def tickValues(self,minVal,maxVal,size):
        # Interior ticks fall on round numbers (1-2-2.5-5 steps); the exact range ends are drawn separately
        # as editable endpoint labels, so ticks too close to them are dropped instead of overlapping.
        minVal,maxVal=sorted((minVal,maxVal));span=maxVal-minVal
        if span<=0 or size<=0:return []
        step=self._step();gap=(44 if self.orientation in ('bottom','top') else 26)*span/size
        first=math.ceil((minVal+gap)/step-1e-9);last=math.floor((maxVal-gap)/step+1e-9)
        values=[index*step for index in range(first,last+1)][:50]
        return [(step,values)]
    def tickStrings(self,values,scale,spacing):
        if getattr(self,'tick_format',None):return [self.tick_format(value) for value in values]
        step=spacing or self._step()
        return [self._format(value,step) for value in values]
    def endpoint_strings(self):
        step=self._step()
        return [self._format(value,step,endpoint=True) for value in sorted(self.range)]
    def _format(self,value,step,endpoint=False):
        if self.logMode:
            return format_count(10**value, 2 if endpoint else 1)
        if self.integer_axis():
            return str(int(round(value)))
        return format_tick(value,step,extra=1 if endpoint else 0)
    def drawPicture(self,p,axisSpec,tickSpecs,textSpecs):
        ends=self.endpoint_strings()
        super().drawPicture(p,axisSpec,tickSpecs,textSpecs)
        if not self.style['showValues'] or self.owner is None:return
        p.save();p.setClipping(False);p.setPen(self.textPen());font=QFont('Segoe UI');font.setPixelSize(12);p.setFont(font)
        metrics=p.fontMetrics()
        for index,text in enumerate(ends):
            width=metrics.horizontalAdvance(text)
            # Endpoint labels sit inside the axis span so neighbouring plots can share a narrow gutter.
            if self.orientation=='bottom':point=QPointF(self.geometry().width()-width if index else 0,20)
            else:
                top=(index==0) if self.owner._invert_y else (index==1)
                point=QPointF(self.geometry().width()-AXIS_TEXT_OFFSET-width,metrics.ascent()-1 if top else self.geometry().height()-1)
            if self._hovered==('min' if index==0 else 'max'):
                # Editable affordance: the hovered limit turns blue with a dotted underline.
                blue=QColor(palette()['blue']);p.setPen(blue);p.drawText(point,text)
                underline=QPen(blue,1,Qt.DotLine);underline.setCosmetic(True);p.setPen(underline)
                p.drawLine(QPointF(point.x(),point.y()+2),QPointF(point.x()+width,point.y()+2));p.setPen(self.textPen())
            else:
                p.drawText(point,text)
        p.restore()
    def _updateMaxTextSize(self,x):
        super()._updateMaxTextSize(x);self._place_label()
    def resizeEvent(self,ev=None):
        super().resizeEvent(ev);self._place_label()
    def _place_label(self):
        # pyqtgraph pins the title to the far edge of the axis; keep it next to the tick text instead.
        if self.label is None or not self.label.isVisible() or not self.style['showValues']:return
        br=self.label.boundingRect();doc=self.label.document().documentMargin()
        if self.orientation=='bottom':
            self.label.setPos(QPointF(int(self.size().width()/2-br.width()/2),AXIS_TEXT_OFFSET+min(self.textHeight,18)+AXIS_TITLE_GAP-doc))
        elif self.orientation=='left':
            x=self.size().width()-AXIS_TEXT_OFFSET-self.textWidth-AXIS_TITLE_GAP-br.height()+doc
            self.label.setPos(QPointF(max(-doc,int(x)),int(self.size().height()/2+br.width()/2)))
    def mouseClickEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.owner is not None and not ev.double():
            endpoint = self.endpoint_at(ev.pos())
            if endpoint:
                self.owner.begin_axis_edit('x' if self.orientation in ('bottom', 'top') else 'y', endpoint)
                ev.accept()
                return
        super().mouseClickEvent(ev)

class EditablePlot(pg.PlotWidget):
    pointPicked = Signal(float, float)
    def __init__(self, parent=None, grid=True, invert_y=False, **kwargs):
        self._grid = bool(grid)
        self._invert_y = bool(invert_y)
        self._editor = None
        self._edit_axis = None
        self._edit_endpoint = None
        self._manual_ranges = {}
        self._linked_axes={}
        self._data_bounds = {}
        self._limits = {}
        self.clamp_to_data = False
        self._x_peers = []
        self._editor_busy = False
        self.readout_units = {}
        self._hidden_series = set()
        bottom = InlineAxis('bottom')
        left = InlineAxis('left')
        view_box = ZoomViewBox()
        super().__init__(parent=parent, axisItems={'bottom': bottom, 'left': left}, viewBox=view_box, **kwargs)
        view_box.owner = self
        self.zoom_group = ZoomGroup(self)
        bottom.owner = self
        left.owner = self
        self.bottom_axis = bottom
        self.left_axis = left
        self.plotItem = self.getPlotItem()
        self.plotItem.setMenuEnabled(False)
        try:
            self.plotItem.hideButtons()
        except Exception:
            pass
        self.plotItem.setMouseEnabled(x=True, y=True)
        self.plotItem.setDownsampling(auto=True,mode='peak')
        self.plotItem.setClipToView(True)
        if self._invert_y:
            try:
                self.plotItem.invertY(True)
            except Exception:
                pass
        # The frame is drawn over the view box itself; axis lines sit one pixel outside it and left a
        # background-coloured seam between image data and the frame.
        self._frame=QGraphicsRectItem(self.plotItem.vb);self._frame.setZValue(1e9);self._frame.setBrush(Qt.NoBrush)
        self._frame.setAcceptedMouseButtons(Qt.NoButton);self._frame.setAcceptHoverEvents(False)
        self.plotItem.vb.sigResized.connect(self._place_frame)
        self._apply_style()
        self.set_plot_margins(*PLOT_MARGINS)
        self._empty=pg.TextItem('',anchor=(.5,.5),color=colors['muted'] if (colors:=palette()) else '#63748a')
        font=QFont('Segoe UI');font.setPixelSize(12);self._empty.setFont(font);self._empty.setZValue(50);self.plotItem.addItem(self._empty,ignoreBounds=True)
        self.plotItem.vb.sigRangeChanged.connect(self._position_empty)
        self._build_zoom_overlays()
        self.setFocusPolicy(Qt.ClickFocus)
        self.plotItem.vb.sigResized.connect(self._place_overlays)
        self.plotItem.vb.sigResized.connect(self._place_pick_label);self.plotItem.vb.sigRangeChanged.connect(self._place_pick_label)
        self.scene().sigMouseMoved.connect(self._hover_moved)
    def _build_zoom_overlays(self):
        # Floating controls inside the plot frame: readout top-left, Back / Full view top-right.
        self._readout = QLabel(self)
        self._readout.setObjectName('plotReadout')
        self._readout.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._readout.hide()
        self._zoom_bar = QWidget(self)
        self._zoom_bar.setObjectName('plotZoomBar')
        bar = QHBoxLayout(self._zoom_bar); bar.setContentsMargins(0, 0, 0, 0); bar.setSpacing(4)
        self._back_chip = QPushButton('‹ Back')
        self._back_chip.setToolTip('Previous zoom (Backspace)')
        self._back_chip.clicked.connect(lambda: self.zoom_group.undo())
        self._fit_chip = QPushButton('Full view')
        self._fit_chip.setToolTip('Show all data (double-click the plot)')
        self._fit_chip.clicked.connect(self.reset_view)
        for chip in (self._back_chip, self._fit_chip):
            chip.setObjectName('plotChip'); chip.setCursor(Qt.PointingHandCursor); chip.setFocusPolicy(Qt.NoFocus); bar.addWidget(chip)
        self._zoom_bar.hide()
        self._editor = QLineEdit(self)
        self._editor.setObjectName('axisEndpointEdit')
        self._editor.installEventFilter(self)
        self._editor.hide()
    def _view_rect(self):
        return self.mapFromScene(self.plotItem.vb.sceneBoundingRect()).boundingRect()
    def _place_overlays(self, *args):
        rect = self._view_rect()
        self._readout.move(rect.left() + 6, rect.top() + 6)
        self._zoom_bar.adjustSize()
        self._zoom_bar.move(rect.right() - self._zoom_bar.width() - 5, rect.top() + 6)
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_zoom_bar'):
            self._refresh_zoom_bar()
            self._place_overlays()
    def set_plot_margins(self,left,right,top,bottom):
        self.plotItem.layout.setContentsMargins(0,0,0,0);self.plotItem.layout.setHorizontalSpacing(0);self.plotItem.layout.setVerticalSpacing(0)
        self.left_axis.setWidth(left);self.bottom_axis.setHeight(bottom)
        for name,size in [('top',top),('right',right)]:
            self.plotItem.showAxis(name);axis=self.plotItem.getAxis(name);axis.setStyle(showValues=False,tickLength=0);axis.setGrid(False);axis.setTicks([[],[]]);axis.setPen(pg.mkPen(None));axis.setTickPen(pg.mkPen(None));axis.setHeight(size) if name=='top' else axis.setWidth(size)
    def _place_frame(self,*args):
        rect=self.plotItem.vb.rect();dpr=self.devicePixelRatioF() or 1.0;half=.5/dpr
        self._frame.setRect(rect.adjusted(half,half,-half,-half))
    def set_empty(self,text):
        if text:self.clear_pick()
        self._empty.setText(text,color=palette()['muted']);self._empty.setVisible(bool(text));self._position_empty()
        self._show_grid()
    def _position_empty(self,*args):
        if hasattr(self,'_empty'):
            x,y=self.viewRange();self._empty.setPos((x[0]+x[1])/2,(y[0]+y[1])/2)
    def _show_grid(self):
        # Axis grid lines draw above view-box items, so an empty plot drops its grid instead of striking through the message.
        grid=self._grid and not (hasattr(self,'_empty') and self._empty.isVisible())
        self.plotItem.showGrid(x=grid,y=grid,alpha=1.0 if grid else 0.0)
        # Without grid lines the zero-length ticks would show as dots beside the labels; paint them in the card colour.
        colors=palette()
        for axis in (self.bottom_axis,self.left_axis):axis.setTickPen(pg.mkPen(colors['chart_grid'] if grid else colors['surface']))
    def _apply_style(self):
        colors = palette()
        self.setBackground(colors['surface'])
        self.plotItem.vb.setBackgroundColor(colors['plot'])
        # No frame: the plot sits flat on its card and the light grid marks the data area.
        self._frame.setPen(pg.mkPen(None))
        for axis in (self.bottom_axis, self.left_axis):
            axis.setPen(pg.mkPen(None))
            axis.setTextPen(pg.mkPen(colors['muted']))
            axis.setTickPen(pg.mkPen(colors['chart_grid']))
            font=QFont('Segoe UI');font.setPixelSize(12);axis.setTickFont(font);axis.setStyle(autoExpandTextSpace=False)
            axis.enableAutoSIPrefix(False)
            try:
                axis.setStyle(tickLength=0, tickTextOffset=4,hideOverlappingLabels=False)
            except Exception:
                pass
        try:
            self._show_grid()
            for name in ('top','right'):self.plotItem.getAxis(name).setGrid(False)
        except Exception:
            pass
    def update_theme(self):
        self._apply_style()
        if hasattr(self,'_empty'):self._empty.setColor(palette()['muted'])
        self._style_cursor_lines()
        if getattr(self,'_pick',None):self._restore_pick()
    def _style_cursor_lines(self):
        # Draggable cursors: blue on hover (pyqtgraph defaults to red) and readable value tags.
        colors=palette();hover=pg.mkPen(colors['blue'],width=1.5)
        tag=QColor(colors['surface']);tag.setAlpha(225)
        for item in self.plotItem.items:
            lines=item.lines if isinstance(item,pg.LinearRegionItem) else [item] if isinstance(item,pg.InfiniteLine) else []
            for line in lines:
                line.setHoverPen(hover)
                label=getattr(line,'label',None)
                if label is not None:
                    label.setColor(colors['text']);label.fill=pg.mkBrush(tag)
    # Legend toggles: every drawn item carries the keys of the legend entries it belongs to and is hidden while
    # any of them is switched off. Redraws re-create items, so the hidden set lives on the plot.
    def tag_series(self, item, *keys):
        item._series_keys = keys
        item.setVisible(not (set(keys) & self._hidden_series))
        return item
    def series_hidden(self, key):
        return key in self._hidden_series
    def _series_items(self):
        return list(self.plotItem.items)
    def _series_changed(self):
        pass
    def set_series_visible(self, key, visible):
        if visible:self._hidden_series.discard(key)
        else:self._hidden_series.add(key)
        for item in self._series_items():
            keys = getattr(item, '_series_keys', None)
            if keys and key in keys:
                item.setVisible(not (set(keys) & self._hidden_series))
        self._series_changed()
    AXIS_LABEL_STYLE = {'font-family': 'Segoe UI', 'font-size': '12px'}
    def set_x_label(self, text):
        self.bottom_axis.setLabel(text, color=palette()['muted'], **self.AXIS_LABEL_STYLE)
    def set_y_label(self, text):
        self.left_axis.setLabel(text, color=palette()['muted'], **self.AXIS_LABEL_STYLE)
    def set_axis_bounds(self, axis, low, high, manual=False):
        # Every redraw sets bounds after replacing its series, so a selection on a removed series is dropped here.
        self._validate_pick()
        source=self._linked_axes.get(axis)
        if source is not None:
            return source.set_axis_bounds(axis,low,high,manual=True) if manual else True
        log_mode=self._log_mode(axis)
        saved=self._manual_ranges.get(axis)
        if manual:
            self._manual_ranges[axis]=(float(low),float(high),log_mode)
        else:
            # Remember the data extent so Full view / undo can return to it without waiting for new data.
            self._data_bounds[axis]=(float(low),float(high),log_mode)
            if self.clamp_to_data:
                self._apply_limits(axis,float(low),float(high))
            if saved and saved[2]==log_mode:
                low,high=saved[:2]
            elif saved:
                self._manual_ranges.pop(axis,None)
                self.zoom_group.changed()
        low = float(low)
        high = float(high)
        if not math.isfinite(low) or not math.isfinite(high) or high <= low:
            return False
        if axis == 'x':
            self.plotItem.vb.setXRange(low, high, padding=0)
        else:
            self.plotItem.vb.setYRange(low, high, padding=0)
        return True
    def auto_axis(self, axis):
        if axis in self._linked_axes:
            return
        self._data_bounds[axis]='auto'
        if axis not in self._manual_ranges:
            self.plotItem.vb.enableAutoRange(axis=pg.ViewBox.XAxis if axis=='x' else pg.ViewBox.YAxis,enable=True)
    def link_axis(self,axis,source):
        self._linked_axes[axis]=source
        if axis=='x':self.plotItem.vb.setXLink(source.plotItem.vb)
        else:self.plotItem.vb.setYLink(source.plotItem.vb)
        source.zoom_group.merge(self.zoom_group)
    def sync_x_with(self,*others):
        '''Zooming the x axis of any of these plots applies the same range to the others while their x titles match.'''
        plots=[self,*others]
        for plot in plots[1:]:
            self.zoom_group.merge(plot.zoom_group)
        for plot in plots:
            plot._x_peers=[other for other in plots if other is not plot]
    # ---- user view: zoom, pan, reset, undo ----
    def _log_mode(self,axis):
        return bool((self.bottom_axis if axis=='x' else self.left_axis).logMode)
    def x_unit(self):
        return self.bottom_axis.labelText
    def _axis_owner(self,axis):
        return self._linked_axes.get(axis) or self
    def _has_user_view(self):
        return any(axis in self._axis_owner(axis)._manual_ranges for axis in ('x','y'))
    def _matching_peers(self):
        return [peer for peer in self._x_peers if peer.x_unit()==self.x_unit()]
    def _apply_limits(self,axis,low,high):
        # Image-like plots (TRPL map, CCD frame) may zoom in, but pan / zoom-out stops at the data edge.
        if not high>low:return
        linked=tuple(plot for plot in self.zoom_group.members if plot._linked_axes.get(axis) is self)
        key=(low,high,tuple(id(plot) for plot in linked))
        if self._limits.get(axis)==key:return
        self._limits[axis]=key
        limits={'xMin':low,'xMax':high} if axis=='x' else {'yMin':low,'yMax':high}
        for plot in (self,)+linked:
            plot.plotItem.vb.setLimits(**limits)
    def _set_manual(self,axis,low,high):
        if not high>low:return
        owner=self._axis_owner(axis);bounds=owner._data_bounds.get(axis)
        if bounds and bounds!='auto' and bounds[2]==owner._log_mode(axis):
            tolerance=(bounds[1]-bounds[0])*1e-6
            if abs(low-bounds[0])<=tolerance and abs(high-bounds[1])<=tolerance:
                # Zoomed back out to exactly the data extent: that is the full view, not a manual range.
                owner._clear_axis(axis);return
        self.set_axis_bounds(axis,low,high,manual=True)
    def apply_user_range(self,x=None,y=None):
        self.zoom_group.push()
        if x is not None:
            self._set_manual('x',*x)
            for peer in self._matching_peers():peer._set_manual('x',*x)
        if y is not None:
            self._set_manual('y',*y)
        self.zoom_group.changed()
    def _commit_view(self,axes):
        # Called while the view box is panned or wheel-zoomed: keep the new range so live data refreshes respect it.
        x_range,y_range=self.viewRange()
        if 'x' in axes:
            self._set_manual('x',*sorted(x_range))
            for peer in self._matching_peers():peer._set_manual('x',*sorted(x_range))
        if 'y' in axes:
            self._set_manual('y',*sorted(y_range))
        self.zoom_group.changed()
    def _clear_axis(self,axis):
        source=self._linked_axes.get(axis)
        if source is not None:
            return source._clear_axis(axis)
        self._manual_ranges.pop(axis,None)
        bounds=self._data_bounds.get(axis)
        if bounds and bounds!='auto' and bounds[2]==self._log_mode(axis) and bounds[1]>bounds[0]:
            (self.plotItem.vb.setXRange if axis=='x' else self.plotItem.vb.setYRange)(bounds[0],bounds[1],padding=0)
        else:
            self.plotItem.vb.enableAutoRange(axis=pg.ViewBox.XAxis if axis=='x' else pg.ViewBox.YAxis,enable=True)
    def _restore_manual(self,manual):
        for axis in ('x','y'):
            if axis in self._linked_axes:
                continue
            saved=manual.get(axis)
            if saved and saved[2]==self._log_mode(axis):
                self.set_axis_bounds(axis,saved[0],saved[1],manual=True)
            elif axis in self._manual_ranges or saved:
                self._clear_axis(axis)
    def _family(self):
        # Plots sharing an axis link with this one (map + decay) act as one figure for reset and the zoom bar.
        family=[self]
        for plot in self.zoom_group.members:
            if plot is not self and (self in plot._linked_axes.values() or plot in self._linked_axes.values()):
                family.append(plot)
        return family
    def reset_view(self):
        family=self._family()
        peers=[peer for peer in self._matching_peers() if 'x' in peer._axis_owner('x')._manual_ranges]
        if not any(plot._has_user_view() for plot in family) and not peers:
            return
        self.zoom_group.push()
        for plot in family:
            for axis in ('x','y'):
                plot._clear_axis(axis)
        for peer in peers:
            peer._clear_axis('x')
        self.zoom_group.changed()
    def _refresh_zoom_bar(self):
        if not hasattr(self,'_zoom_bar'):
            return
        family=self._family()
        zoomed=any(plot._has_user_view() for plot in family)
        self._back_chip.setVisible(bool(self.zoom_group.stack))
        self._zoom_bar.adjustSize()
        # A narrow companion plot (the decay beside the map) leaves the controls to the wider plot it is linked to.
        wider=[plot for plot in family if plot is not self and plot._view_rect().width()>self._view_rect().width()]
        fits=self._view_rect().width()>=self._zoom_bar.width()+12
        visible=zoomed and fits and not wider
        self._zoom_bar.setVisible(visible)
        if visible:
            self._place_overlays();self._zoom_bar.raise_()
    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape and getattr(self,'_pick',None):
            self.clear_pick();event.accept();return
        if event.key()==Qt.Key_Backspace and self.zoom_group.undo():
            event.accept();return
        if event.key()==Qt.Key_Home:
            self.reset_view();event.accept();return
        super().keyPressEvent(event)
    def mousePressEvent(self,event):
        if event.button()==Qt.BackButton:
            self.zoom_group.undo();event.accept();return
        super().mousePressEvent(event)
    # ---- hover readout ----
    def _reading(self,axis,value):
        if self._log_mode(axis):
            value=10**value
        label=self.readout_units.get(axis,(self.bottom_axis if axis=='x' else self.left_axis).labelText)
        return format_reading(value,label)
    def readout_text(self,x,y):
        return f'{self._reading("x",x)}  ·  {self._reading("y",y)}'
    def range_text(self,x_range,y_range):
        parts=[]
        for axis,span in (('x',x_range),('y',y_range)):
            if span is None:continue
            low,high=(10**span[0],10**span[1]) if self._log_mode(axis) else span
            prefix,unit=label_parts(self.readout_units.get(axis,(self.bottom_axis if axis=='x' else self.left_axis).labelText))
            parts.append(f'{prefix} {fmt(low,5)} – {fmt(high,5)} {unit}'.strip())
        return '  ×  '.join(parts)
    # ---- point picking ----
    PICK_RADIUS = 9
    def make_pickable(self, item, x_label=None, y_label=None, key='x'):
        '''Let a scatter series be clicked: the nearest point is ringed and labelled with its coordinates.

        key names the coordinate that identifies a point across data refreshes, so a selection survives the
        series being redrawn (the decay plot draws delay on y).'''
        # A direct hit is taken (and consumed) by the scatter itself; near misses reach the view box. Both pick.
        item.sigPointsClicked.connect(lambda _item, _points, event: self.pick_at(event.scenePos()))
        self._pickables = [entry for entry in getattr(self, '_pickables', []) if entry[0].scene() is not None]
        self._pickables.append((item, x_label, y_label, key))
        self._restore_pick()
    def _pick_points(self, entry):
        xs, ys = entry[0].getOriginalDataset()
        if xs is None or ys is None:
            return None
        xs = np.asarray(xs, dtype=float); ys = np.asarray(ys, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            dx = np.log10(xs) if self._log_mode('x') else xs
            dy = np.log10(ys) if self._log_mode('y') else ys
        return xs, ys, dx, dy
    def _nearest_pick(self, scene_pos):
        view = self.plotItem.vb
        if not view.sceneBoundingRect().contains(scene_pos):
            return None
        point = view.mapSceneToView(scene_pos); width, height = view.viewPixelSize()
        best = None
        for entry in getattr(self, '_pickables', []):
            if entry[0].scene() is None or not entry[0].isVisible():
                continue
            data = self._pick_points(entry)
            if data is None or not len(data[0]):
                continue
            xs, ys, dx, dy = data
            with np.errstate(invalid='ignore'):
                distance = np.hypot((dx - point.x()) / width, (dy - point.y()) / height)
            if not np.isfinite(distance).any():
                continue
            index = int(np.nanargmin(distance))
            if distance[index] <= self.PICK_RADIUS and (best is None or distance[index] < best[0]):
                best = (float(distance[index]), entry, index, xs, ys, dx, dy)
        return best
    def pick_at(self, scene_pos):
        '''Select the point under a click; a click away from every point clears the selection.'''
        found = self._nearest_pick(scene_pos)
        if found is None:
            self.clear_pick()
            return False
        _, entry, index, xs, ys, _, _ = found
        self._show_pick(entry, index)
        self.pointPicked.emit(float(xs[index]), float(ys[index]))
        return True
    def _show_pick(self, entry, index):
        item, x_label, y_label, key = entry
        xs, ys, dx, dy = self._pick_points(entry)
        x, y = float(xs[index]), float(ys[index]); px, py = float(dx[index]), float(dy[index])
        if not (math.isfinite(px) and math.isfinite(py)):
            self.clear_pick(); return
        self._pick = {'entry': entry, 'key': key, 'value': x if key == 'x' else y}
        colors = palette()
        if getattr(self, '_pick_ring', None) is None:
            self._pick_ring = pg.ScatterPlotItem(size=12); self._pick_ring.setZValue(1e6)
            self._pick_label = pg.TextItem('', anchor=(0, 1)); self._pick_label.setZValue(1e6 + 1)
            font = QFont('Segoe UI'); font.setPixelSize(12); self._pick_label.setFont(font)
            self.plotItem.addItem(self._pick_ring, ignoreBounds=True); self.plotItem.addItem(self._pick_label, ignoreBounds=True)
        # The selected point is redrawn larger in the accent colour with a card-coloured rim, apart from the data colour.
        self._pick_ring.setData([px], [py], brush=pg.mkBrush(colors['blue']), pen=pg.mkPen(colors['surface'], width=2))
        self._readout.hide()
        back = QColor(colors['surface']); back.setAlpha(240)
        self._pick_label.fill = pg.mkBrush(back); self._pick_label.border = pg.mkPen(colors['line'])
        self._pick_label.setColor(colors['text'])
        x_text = pick_reading(x, x_label if x_label is not None else self.bottom_axis.labelText)
        y_text = pick_reading(y, y_label if y_label is not None else self.left_axis.labelText)
        self._pick_label.setText(f'{x_text}\n{y_text}')
        self._pick_view_pos = (px, py)
        self._pick_ring.show(); self._pick_label.show()
        self._place_pick_label()
    def _place_pick_label(self, *args):
        '''Put the label beside the selected point on whichever side has room, then clamp it inside the plot
        frame, so it stays readable next to an edge or in a short plot. Re-run on zoom, pan and resize.'''
        label = getattr(self, '_pick_label', None)
        if not getattr(self, '_pick', None) or label is None:
            return
        view = self.plotItem.vb; frame = view.sceneBoundingRect()
        point = view.mapViewToScene(QPointF(*self._pick_view_pos))
        # Zoomed or panned away from the point: no label floating without its point.
        label.setVisible(frame.contains(point))
        if not label.isVisible():
            return
        size = label.textItem.boundingRect(); width, height = size.width(), size.height(); gap = 8; edge = 3
        x = point.x() + gap if point.x() + gap + width <= frame.right() - edge else point.x() - gap - width
        y = point.y() - gap - height if point.y() - gap - height >= frame.top() + edge else point.y() + gap
        x = min(max(x, frame.left() + edge), frame.right() - edge - width)
        y = min(max(y, frame.top() + edge), frame.bottom() - edge - height)
        label.setAnchor((0, 0)); label.setPos(view.mapSceneToView(QPointF(x, y)))
    def clear_pick(self):
        self._pick = None
        for mark in (getattr(self, '_pick_ring', None), getattr(self, '_pick_label', None)):
            if mark is not None:
                mark.hide()
    def _restore_pick(self):
        # After a redraw, follow the selected point into the new series by its identifying coordinate.
        pick = getattr(self, '_pick', None)
        if not pick:
            return
        if pick['entry'][0].scene() is not None:
            index = self._pick_index(pick['entry'], pick['key'], pick['value'])
            self.clear_pick() if index is None else self._show_pick(pick['entry'], index)
            return
        self._pickables = [entry for entry in self._pickables if entry[0].scene() is not None]
        for entry in self._pickables:
            if entry[3] != pick['key']:
                continue
            index = self._pick_index(entry, pick['key'], pick['value'])
            if index is not None:
                self._show_pick(entry, index); return
        self.clear_pick()
    def _pick_index(self, entry, key, value):
        data = self._pick_points(entry)
        if data is None or not len(data[0]):
            return None
        values = data[0] if key == 'x' else data[1]
        with np.errstate(invalid='ignore'):
            matches = np.flatnonzero(np.abs(values - value) <= 1e-9 * max(1.0, abs(value)))
        return int(matches[0]) if matches.size else None
    def _validate_pick(self):
        pick = getattr(self, '_pick', None)
        if pick and pick['entry'][0].scene() is None:
            self._restore_pick()
    def _hover_moved(self,pos):
        view=self.plotItem.vb
        self._validate_pick()
        # A pointing hand over a pickable point says it can be clicked.
        hand=bool(getattr(self,'_pickables',None)) and self._nearest_pick(pos) is not None
        if hand!=getattr(self,'_pick_hover',False):
            self._pick_hover=hand;view.setCursor(Qt.PointingHandCursor if hand else Qt.CrossCursor)
        if getattr(self,'_pick',None):
            # A selected point keeps its coordinates on screen; the readout under the mouse would compete with it.
            self._readout.hide();return
        if self._empty.isVisible() or not view.sceneBoundingRect().contains(pos):
            self._readout.hide();return
        point=view.mapSceneToView(pos)
        try:text=self.readout_text(point.x(),point.y())
        except Exception:text=''
        if not text:
            self._readout.hide();return
        self._readout.setText(text);self._readout.adjustSize()
        if self._readout.width()>self._view_rect().width()-12:
            # Narrow plots (the decay beside the map) stack the values instead of running past the frame.
            self._readout.setText(text.replace('  ·  ','\n'));self._readout.adjustSize()
        self._place_overlays();self._readout.show();self._readout.raise_()
    def leaveEvent(self,event):
        self._readout.hide()
        super().leaveEvent(event)
    def overlay_axis(self, axis):
        '''An extra scale drawn inside the plot (InsideAxisLabels) whose limits the axis editor sets, or None.'''
        return None
    def overlay_limits(self, axis):
        return (0.0, 1.0)
    def set_overlay_limits(self, axis, low, high):
        pass
    def begin_axis_edit(self, axis, endpoint):
        '''Turn the clicked axis limit into a text field in place, styled like the label it replaces.'''
        overlay = self.overlay_axis(axis)
        axis_item = overlay or (self.bottom_axis if axis == 'x' else self.left_axis)
        rect = axis_item.endpoint_rects().get(endpoint)
        if rect is None:
            return
        if overlay is not None:
            low, high = self.overlay_limits(axis)
        else:
            low, high = sorted(self.viewRange()[0 if axis == 'x' else 1])
        value = low if endpoint == 'min' else high
        self._edit_axis = axis
        self._edit_endpoint = endpoint
        editor = self._editor
        text = fmt(value, 8)
        editor.setText(text)
        set_invalid(editor, False)
        width = int(max(64, editor.fontMetrics().horizontalAdvance(text) + 22, rect.width() + 22))
        height = 22
        area = QRect(self.mapFromScene(axis_item.mapToScene(rect.topLeft())), self.mapFromScene(axis_item.mapToScene(rect.bottomRight())))
        if axis == 'x' and endpoint == 'min':
            x = area.left() - 4
            editor.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        else:
            x = area.right() - width + 5
            editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        y = area.center().y() - height // 2
        editor.setGeometry(max(0, min(self.width() - width, x)), max(0, min(self.height() - height, y)), width, height)
        editor.show()
        editor.raise_()
        editor.setFocus()
        editor.selectAll()
    def eventFilter(self, obj, event):
        if obj is self._editor:
            if event.type() == QEvent.KeyPress:
                key = event.key()
                if key == Qt.Key_Escape:
                    self._close_editor()
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter):
                    self._commit_editor()
                    return True
                if key in (Qt.Key_Tab, Qt.Key_Backtab):
                    # Tab moves to the other limit of the same axis.
                    axis, other = self._edit_axis, 'max' if self._edit_endpoint == 'min' else 'min'
                    if self._commit_editor():
                        self.begin_axis_edit(axis, other)
                    return True
            elif event.type() == QEvent.FocusOut and self._editor.isVisible() and not self._editor_busy:
                if not self._commit_editor():
                    self._close_editor()
        return super().eventFilter(obj, event)
    def _close_editor(self):
        self._editor_busy = True
        try:
            self._editor.hide()
            self.setFocus()
        finally:
            self._editor_busy = False
    def _commit_editor(self):
        try:
            value = float(self._editor.text().strip())
        except Exception:
            value = math.nan
        overlay = self.overlay_axis(self._edit_axis) is not None
        if overlay:
            low, high = self.overlay_limits(self._edit_axis)
        else:
            low, high = sorted(self.viewRange()[0 if self._edit_axis == 'x' else 1])
        if self._edit_endpoint == 'min':
            low = value
        else:
            high = value
        if overlay and math.isfinite(value) and high > low:
            try:self.set_overlay_limits(self._edit_axis, low, high)
            except ValueError as error:
                set_invalid(self._editor, True); self._editor.setToolTip(str(error)); return False
            self._editor.setToolTip(''); self._close_editor(); return True
        if not math.isfinite(value) or high <= low:
            set_invalid(self._editor, True)
            self._editor.setToolTip('Enter a number; the minimum must stay below the maximum')
            return False
        self._editor.setToolTip('')
        self._close_editor()
        self.apply_user_range(x=(low, high) if self._edit_axis == 'x' else None, y=(low, high) if self._edit_axis == 'y' else None)
        return True

class PinnedLines:
    '''Pinned wavelengths as dashed vertical lines, shared by the TRPL map and the Live spectrum. Right-click
    the plot to add one (pinAddRequested), right-click a line to remove it (pinRemoveRequested), drag a line to
    move it (pinMoved(key, wavelength, finished)). The window owns the list and redraws every plot from it.'''
    PIN_HIT_PX = 6
    def _init_pins(self):
        self._pin_lines = {}; self._pin_guard = False
        self.scene().sigMouseClicked.connect(self._pin_clicked)
    def set_pins(self, pins):
        '''[(legend key, wavelength, palette colour key, label)]; existing lines are moved, not recreated, so a
        drag in progress is never interrupted.'''
        colors = palette(); keys = {pin[0] for pin in pins or []}
        for key in [key for key in self._pin_lines if key not in keys]:
            self.plotItem.removeItem(self._pin_lines.pop(key))
        self._pin_guard = True
        try:
            for key, wave, color, label in pins or []:
                line = self._pin_lines.get(key)
                if line is None:
                    line = pg.InfiniteLine(pos=float(wave), angle=90, movable=True)
                    line.setZValue(20); self.plotItem.addItem(line, ignoreBounds=True); self.tag_series(line, key)
                    line.sigPositionChanged.connect(lambda item, key=key: None if self._pin_guard else self.pinMoved.emit(key, float(item.value()), False))
                    line.sigPositionChangeFinished.connect(lambda item, key=key: self.pinMoved.emit(key, float(item.value()), True))
                    self._pin_lines[key] = line
                elif not line.moving and abs(float(line.value()) - float(wave)) > 1e-9:
                    line.setPos(float(wave))
                line.setPen(pg.mkPen(colors[color], width=1.2, style=Qt.DashLine)); line.setHoverPen(pg.mkPen(colors[color], width=2.4))
                line.setToolTip(f'{label} · drag to move · right-click to remove')
        finally:
            self._pin_guard = False
    def _pin_clicked(self, event):
        if event.button() != Qt.RightButton or event.double():
            return
        view = self.plotItem.vb; position = event.scenePos()
        if not view.sceneBoundingRect().contains(position):
            return
        for key, line in self._pin_lines.items():
            if line.isVisible() and abs(view.mapViewToScene(QPointF(float(line.value()), 0)).x() - position.x()) <= self.PIN_HIT_PX:
                self.pinRemoveRequested.emit(key); event.accept(); return
        self.pinAddRequested.emit(float(view.mapSceneToView(position).x())); event.accept()

class OverlayViewBox(pg.ViewBox):
    '''A second y scale laid over a plot's view box (same x). It takes no mouse input: zoom, pan, cursors and
    picking all reach the plot underneath.'''
    def __init__(self):
        super().__init__(enableMenu=False)
        self.setMouseEnabled(False, False); self.setAcceptedMouseButtons(Qt.NoButton); self.setAcceptHoverEvents(False)
        # Its y range is set explicitly and its x follows the plot; auto-range would move both.
        self.disableAutoRange()
    def wheelEvent(self, ev, axis=None):
        ev.ignore()
    def mouseDragEvent(self, ev, axis=None):
        ev.ignore()
    def mouseClickEvent(self, ev):
        ev.ignore()

class InsideAxisLabels(pg.GraphicsObject):
    '''An overlay scale drawn just inside the plot's right edge in the colour of its data, so it needs no gutter
    of its own; the legend says which data it belongs to. Its two limits, at the top and bottom, are editable
    like the main axes' (click, type, Enter). Only those labels take the mouse (see shape()); clicks anywhere
    else reach the plot underneath.'''
    def __init__(self, view, color_key, owner=None, axis='noise'):
        super().__init__(view)
        self.view = view; self.color_key = color_key; self.owner = owner; self.axis = axis
        self.log = False; self.suffix = ''; self._hovered = None
        self.setAcceptedMouseButtons(Qt.LeftButton); self.setAcceptHoverEvents(True); self.setZValue(1e6)
        view.sigRangeChanged.connect(lambda *args: (self.prepareGeometryChange(), self.update()))
        view.sigResized.connect(self._resized)
    def _resized(self, *args):
        self.prepareGeometryChange(); self.update()
    def boundingRect(self):
        return QRectF(self.view.rect())
    def _font(self):
        font = QFont('Segoe UI'); font.setPixelSize(11); return font
    def value_text(self, value):
        '''A limit as shown and typed: percent in log mode too (the view holds log10 of it).'''
        value = 10 ** value if self.log else value
        return f'{value:.4g}{self.suffix}'
    def _top(self, rect):
        # Below the plot's Back / Full view chips while they are shown.
        bar = getattr(self.owner, '_zoom_bar', None)
        return rect.top() + (bar.height() + 8 if bar is not None and bar.isVisible() else 2)
    def endpoint_rects(self):
        rect = QRectF(self.view.rect()); low, high = self.view.viewRange()[1]; metrics = QFontMetricsF(self._font())
        out = {}
        for name, value in (('max', high), ('min', low)):
            text = self.value_text(value); width = metrics.horizontalAdvance(text)
            top = self._top(rect) if name == 'max' else rect.bottom() - metrics.height() - 2
            out[name] = QRectF(rect.right() - 8 - width, top, width, metrics.height())
        return out
    def overflow(self):
        '''{'max': text, 'min': text} for the data past either end of the scale (in the visible x range).'''
        extent = self.owner.overlay_extent(self.axis) if self.owner is not None else None
        if not extent:
            return {}
        low, high = self.view.viewRange()[1]; low_data, high_data = extent; out = {}
        if self.log:
            low_data, high_data = (math.log10(v) if v > 0 else -math.inf for v in (low_data, high_data))
        if high_data > high + 1e-9 * max(1.0, abs(high)):out['max'] = '▲ ' + self.value_text(high_data)
        if math.isfinite(low_data) and low_data < low - 1e-9 * max(1.0, abs(low)):out['min'] = '▼ ' + self.value_text(low_data)
        return out
    def hint_rects(self):
        metrics = QFontMetricsF(self._font()); ends = self.endpoint_rects(); out = {}
        for name, text in self.overflow().items():
            width = metrics.horizontalAdvance(text); box = ends[name]
            out['fit_' + name] = QRectF(box.left() - 10 - width, box.top(), width, box.height())
        return out
    def _targets(self):
        return {**self.endpoint_rects(), **self.hint_rects()}
    def shape(self):
        path = QPainterPath()
        for box in self._targets().values():path.addRect(box.adjusted(-6, -3, 6, 3))
        return path
    def endpoint_at(self, pos):
        for name, box in self._targets().items():
            if box.adjusted(-6, -3, 6, 3).contains(pos):return name
        return None
    def hoverEvent(self, ev):
        hovered = None if ev.isExit() else self.endpoint_at(ev.pos())
        if hovered:ev.acceptClicks(Qt.LeftButton)
        if hovered != self._hovered:
            self._hovered = hovered
            if hovered and hovered.startswith('fit_'):
                end = 'above' if hovered == 'fit_max' else 'below'
                self.setCursor(Qt.PointingHandCursor); self.setToolTip(f'The noise goes {end} the scale here · click to widen this end to include it')
            elif hovered:self.setCursor(Qt.PointingHandCursor); self.setToolTip('Click to type this limit')
            else:self.unsetCursor(); self.setToolTip('')
            self.update()
    def mouseClickEvent(self, ev):
        target = self.endpoint_at(ev.pos()) if ev.button() == Qt.LeftButton and not ev.double() else None
        if target and self.owner is not None:
            if target.startswith('fit_'):self.owner.widen_overlay(self.axis, target[4:])
            else:self.owner.begin_axis_edit(self.axis, target)
            ev.accept()
        else:
            ev.ignore()
    def ticks(self):
        low, high = self.view.viewRange()[1]
        if not math.isfinite(low) or not math.isfinite(high) or high <= low:
            return []
        if self.log:
            return [(float(v), format_count(10 ** v, 2) + self.suffix) for v in range(math.ceil(low), math.floor(high) + 1)]
        step = nice_step(high - low, 4)
        first = math.ceil(low / step - 1e-9); last = math.floor(high / step + 1e-9)
        return [(index * step, format_tick(index * step, step) + self.suffix) for index in range(first, last + 1)][:20]
    def paint(self, painter, option, widget=None):
        rect = QRectF(self.view.rect()); low, high = self.view.viewRange()[1]
        if rect.height() < 40 or not high > low:
            return
        colors = palette(); color = QColor(colors[self.color_key])
        font = self._font(); painter.setFont(font); metrics = QFontMetricsF(font)
        halo = QColor(colors['plot']); halo.setAlpha(215)
        tick = QPen(color, 1); tick.setCosmetic(True)
        ends = self.endpoint_rects()
        for value, text in self.ticks():
            y = rect.bottom() - (value - low) / (high - low) * rect.height()
            # Interior ticks only: the limits are drawn as editable labels and nothing may crowd them.
            if y < ends['max'].bottom() + 6 or y > ends['min'].top() - 6:
                continue
            painter.setPen(tick); painter.drawLine(QPointF(rect.right() - 5, y), QPointF(rect.right(), y))
            width = metrics.horizontalAdvance(text)
            painter.fillRect(QRectF(rect.right() - 8 - width - 2, y - metrics.height() / 2, width + 4, metrics.height()), halo)
            painter.setPen(color); painter.drawText(QPointF(rect.right() - 8 - width, y + metrics.ascent() / 2 - 1), text)
        for name, box in ends.items():
            text = self.value_text(high if name == 'max' else low); baseline = box.top() + metrics.ascent()
            painter.fillRect(box.adjusted(-2, 0, 2, 0), halo)
            if self._hovered == name:
                # Same editable affordance as the main axes: blue with a dotted underline.
                blue = QColor(colors['blue']); painter.setPen(blue); painter.drawText(QPointF(box.left(), baseline), text)
                underline = QPen(blue, 1, Qt.DotLine); underline.setCosmetic(True); painter.setPen(underline)
                painter.drawLine(QPointF(box.left(), baseline + 2), QPointF(box.right(), baseline + 2))
            else:
                painter.setPen(color); painter.drawText(QPointF(box.left(), baseline), text)
        # Data past an end: a small warning-coloured tag beside that limit (click widens the scale to it).
        warning = QColor(colors['warning']); texts = self.overflow()
        for name, box in self.hint_rects().items():
            painter.fillRect(box.adjusted(-2, 0, 2, 0), halo)
            painter.setPen(QColor(colors['blue']) if self._hovered == name else warning)
            painter.drawText(QPointF(box.left(), box.top() + metrics.ascent()), texts[name[4:]])

class SpectrumPlot(PinnedLines, EditablePlot):
    wavePicked=Signal(float)
    pinAddRequested = Signal(float)
    pinRemoveRequested = Signal(str)
    pinMoved = Signal(str, float, bool)
    def __init__(self, parent=None, grid=True):
        super().__init__(parent=parent, grid=grid)
        self._init_pins()
        self._items = []
        self._marker_guard=False
        # Noise on its own scale (right side, labelled inside the plot), sharing the wavelength axis.
        self.noise_view = OverlayViewBox(); self.noise_view.setParentItem(self.plotItem.vb)
        self.noise_view.setXLink(self.plotItem.vb)
        self.plotItem.vb.sigResized.connect(self._place_noise_view); self._place_noise_view()
        self.noise_axis = InsideAxisLabels(self.noise_view, 'noise', owner=self); self.noise_axis.suffix = '%'; self.noise_axis.hide()
        self._noise_item = None; self._noise_curve = None; self._noise_limit = None; self._noise_ratio = None
        self._limit_item = None
        # A fixed scale, never fitted to the data, so the noise can be read against the same range frame after
        # frame. Set by typing its limits; one range per Y mode, in percent.
        self.noise_range = {'linear': (0.0, 10.0), 'log': (0.1, 100.0)}; self._noise_log = None
        self._apply_noise_range(False)
        self.band=pg.LinearRegionItem(values=(595,605),movable=False,brush=pg.mkBrush(90,130,180,25),pen=pg.mkPen(None))
        self.band.setZValue(-1);self.plotItem.addItem(self.band)
        self.wave_line=pg.InfiniteLine(pos=600,angle=90,movable=True,pen=pg.mkPen(palette()['cursor'],style=Qt.DashLine),label='{value:.2f} nm',labelOpts={'position':.95})
        self.plotItem.addItem(self.wave_line)
        self.wave_line.sigPositionChanged.connect(lambda line:self.wavePicked.emit(float(line.value())) if not self._marker_guard else None)
        self.wave_line.hide();self.band.hide()
    def set_marker(self,wave,half_window=5,visible=True):
        self._marker_guard=True
        try:
            self.wave_line.setPos(wave);self.band.setRegion((wave-half_window,wave+half_window))
            self.wave_line.setVisible(visible);self.band.setVisible(False);self.wave_line.label.hide()
        finally:self._marker_guard=False
    def _clear(self):
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
    def overlay_axis(self, axis):
        return self.noise_axis if axis == 'noise' else None
    def overlay_limits(self, axis):
        return self.noise_range['log' if self._noise_log else 'linear']
    def overlay_extent(self, axis):
        '''(min, max) of the noise drawn within the visible wavelength range, or None.'''
        if axis != 'noise' or self._noise_curve is None or self.series_hidden('noise'):
            return None
        xs, ys = self._noise_curve; x0, x1 = self.plotItem.vb.viewRange()[0]
        shown = ys[(xs >= min(x0, x1)) & (xs <= max(x0, x1)) & np.isfinite(ys)]
        if self._noise_log:shown = shown[shown > 0]
        return (float(shown.min()), float(shown.max())) if shown.size else None
    def widen_overlay(self, axis, end):
        '''Move one end of the noise scale to a round value just past the data (1-2-5 steps on a log scale).'''
        extent = self.overlay_extent(axis)
        if not extent:
            return
        low, high = self.overlay_limits(axis)
        def round_log(value, up):
            exponent = math.floor(math.log10(value)); steps = [1, 2, 5, 10]
            candidates = [step * 10 ** exponent for step in steps]
            return min(c for c in candidates if c >= value) if up else max(c for c in [0.5 * 10 ** exponent] + candidates if c <= value)
        if end == 'max':
            value = extent[1] * 1.1
            if self._noise_log:high = round_log(value, True)
            else:step = nice_step(value, 4); high = math.ceil(value / step) * step
        else:
            if self._noise_log:low = round_log(extent[0] * .9, False)
            else:low = 0.0 if extent[0] >= 0 else -math.ceil(-extent[0] / nice_step(-extent[0], 4)) * nice_step(-extent[0], 4)
        self.set_noise_range(low, high)
    def set_overlay_limits(self, axis, low, high):
        self.set_noise_range(low, high)
    def _place_noise_view(self, *args):
        self.noise_view.setGeometry(QRectF(self.plotItem.vb.rect()))
    def _series_items(self):
        return list(self.plotItem.items) + list(self.noise_view.addedItems)
    def _series_changed(self):
        self.noise_axis.setVisible(self._noise_curve is not None and not self.series_hidden('noise'))
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'noise_axis'):self.noise_axis.update()
    def _noise_curve_item(self, key):
        # Curve items are kept and refilled: a clipped PlotDataItem taken out of an overlay view looks for its
        # view on the plot widget and fails.
        item = pg.PlotDataItem([], [], antialias=True); item.setDownsampling(auto=True, method='peak')
        self.noise_view.addItem(item); self.tag_series(item, key); return item
    def set_noise(self, x, sigma, scale='linear', limit=None):
        '''Noise per pixel, in % of the signal, on the right-hand scale; None clears it. limit (in %) is the
        shot-noise limit, drawn as a dashed line (its legend entry starts switched off).'''
        if self._noise_item is None:
            self._noise_item = self._noise_curve_item('noise')
            self._limit_item = self._noise_curve_item('shot')
        self._noise_curve = self._noise_limit = self._noise_ratio = None
        if sigma is None or x is None or not len(sigma):
            for item in (self._noise_item, self._limit_item):item.setData([], [])
            self._series_changed(); return
        xs = np.asarray(x, dtype=float)[:len(sigma)]; ys = np.asarray(sigma, dtype=float)[:len(xs)]
        log = scale == 'log'; colors = palette()
        def show(item, values, pen):
            item.setPen(pen); item.setLogMode(False, log)
            item.setData(xs, np.where(values > 0, values, np.nan) if log else values)
        show(self._noise_item, ys, pg.mkPen(colors['noise'], width=1.2))
        if limit is not None:
            limit = np.asarray(limit, dtype=float)[:len(xs)]
            show(self._limit_item, limit, pg.mkPen(colors['muted'], width=1, style=Qt.DashLine))
            self._noise_limit = limit
            with np.errstate(divide='ignore', invalid='ignore'):self._noise_ratio = ys / limit
        else:
            self._limit_item.setData([], [])
        self._noise_curve = (xs, ys)
        if log != self._noise_log:self._apply_noise_range(log)
        self.noise_axis.update(); self._series_changed()
    def _apply_noise_range(self, log=None):
        if log is not None:self._noise_log = bool(log)
        low, high = self.noise_range['log' if self._noise_log else 'linear']
        self.noise_axis.log = self._noise_log
        if self._noise_log:self.noise_view.setYRange(math.log10(low), math.log10(high), padding=0)
        else:self.noise_view.setYRange(low, high, padding=0)
        self.noise_axis.prepareGeometryChange(); self.noise_axis.update()
    def set_noise_range(self, low, high):
        '''Limits of the noise scale in percent, for the current Y mode.'''
        log = bool(self._noise_log)
        if not (math.isfinite(low) and math.isfinite(high) and high > low) or (log and low <= 0):
            raise ValueError('Enter a number; the minimum must stay below the maximum' + (' and above 0 on a log scale' if log else ''))
        self.noise_range['log' if log else 'linear'] = (float(low), float(high)); self._apply_noise_range()
    def readout_text(self, x, y):
        # Read the drawn spectrum (the last curve, i.e. the current one) at the cursor wavelength, and its noise.
        curve = getattr(self, '_readout_curve', None)
        if curve is None or not len(curve[0]):
            return super().readout_text(x, y)
        xs, ys = curve
        index = int(np.nanargmin(np.abs(xs - x)))
        text = f'{format_reading(xs[index], self.bottom_axis.labelText)}  ·  {format_reading(ys[index], self.left_axis.labelText)}'
        if self._noise_curve is not None and not self.series_hidden('noise'):
            nx, ny = self._noise_curve
            near = int(np.nanargmin(np.abs(nx - x)))
            if math.isfinite(ny[near]):
                text += f'  ·  σ {ny[near]:.2g} %'
                ratio = self._noise_ratio[near] if self._noise_ratio is not None and not self.series_hidden('shot') else math.nan
                if math.isfinite(ratio):text += f' ({ratio:.2g}× shot limit)'
        return text
    def set_spectra(self, curves, x_label='Wavelength / nm', y_label='Counts', scale='linear'):
        self.set_empty('Start preview or acquisition' if not curves else '')
        self._clear()
        self._readout_curve = None
        try:
            self.plotItem.setLogMode(x=False, y=(scale == 'log'))
        except Exception:
            pass
        xmin = None
        xmax = None
        colors = palette()
        for curve in curves or []:
            if not curve:
                continue
            x, y, color = curve[0], curve[1], curve[2]
            dashed = bool(curve[3]) if len(curve) > 3 else False
            width = curve[4] if len(curve) > 4 else 1.5
            try:
                xa = np.asarray(x, dtype=float)
                ya = np.asarray(y, dtype=float)
            except Exception:
                continue
            if xa.size == 0 or ya.size == 0:
                continue
            if scale == 'log':
                ya = np.where(ya > 0, ya, np.nan)
            pen = pg.mkPen(color, width=width, style=Qt.DashLine if dashed else Qt.SolidLine)
            item = self.plotItem.plot(xa, ya, antialias=True,pen=pen)
            if len(curve) > 5 and curve[5]:self.tag_series(item, curve[5])
            self._items.append(item)
            self._readout_curve = (xa, ya)
            local_min = float(np.nanmin(xa))
            local_max = float(np.nanmax(xa))
            xmin = local_min if xmin is None else min(xmin, local_min)
            xmax = local_max if xmax is None else max(xmax, local_max)
        self.set_x_label(x_label)
        self.set_y_label(y_label)
        if xmin is None or xmax is None or xmax <= xmin:
            self.set_axis_bounds('x', 0, 1024)
        else:
            self.set_axis_bounds('x', xmin, xmax)
            try:
                self.auto_axis('y')
            except Exception:
                pass
    def set_spectrum(self, x, current, background=None, x_label='Wavelength / nm', scale='linear'):
        colors = palette()
        curves = []
        if background is not None and len(background):
            curves.append((x[:len(background)], background, colors['amber'], True, 1.4, 'background'))
        if current is not None and len(current):
            curves.append((x[:len(current)], current, colors['data'], False, 1.7, 'current'))
        self.set_spectra(curves, x_label, 'Counts', scale)

class MapPlot(PinnedLines, EditablePlot):
    delayPicked = Signal(float)
    wavePicked = Signal(float)
    pinAddRequested = Signal(float)
    pinRemoveRequested = Signal(str)
    pinMoved = Signal(str, float, bool)
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=False, invert_y=True)
        self.clamp_to_data = True
        self._settings = False
        self._x_axis = None
        self._delays = []
        self._saturated = {}
        self.image_item = pg.ImageItem(axisOrder='row-major')
        self.image_item.setZValue(-20)
        self.plotItem.addItem(self.image_item)
        self.saturated_item = QGraphicsPathItem()
        pen = QPen(QColor(SATURATED_COLOR), 2); pen.setCosmetic(True); pen.setJoinStyle(Qt.MiterJoin)
        self.saturated_item.setPen(pen); self.saturated_item.setBrush(QColor(SATURATED_COLOR))
        self.saturated_item.setZValue(-15); self.saturated_item.setVisible(False)
        self.plotItem.addItem(self.saturated_item)
        colors = palette()
        self.delay_line = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine),label='Δt = {value:.4g} ps',labelOpts={'position':.85})
        self.wave_line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine),label='{value:.2f} nm',labelOpts={'position':.95})
        self.plotItem.addItem(self.delay_line)
        self.plotItem.addItem(self.wave_line)
        self.delay_line.sigPositionChanged.connect(self._delay_moved)
        self.wave_line.sigPositionChanged.connect(self._wave_moved)
        self.wave_line.label.hide();self.wave_line.sigPositionChangeFinished.connect(lambda:self.wave_line.label.hide())
        try:
            self.scene().sigMouseClicked.connect(self._clicked)
        except Exception:
            pass
        self._init_pins()
        self.set_x_label('Wavelength / nm')
        self.set_y_label('Delay / ps')
        self.update_theme()
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'delay_line'):
            colors = palette()
            self.delay_line.setPen(pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
            self.wave_line.setPen(pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
    def readout_text(self, x, y):
        # Wavelength and delay under the cursor plus the measured counts of that map pixel.
        text = super().readout_text(x, y)
        x_values = getattr(self, '_readout_x', None)
        cache = getattr(self, '_value_cache', {})
        if x_values is None or not self._delays or not cache:
            return text
        delays = np.asarray(self._delays, dtype=float)
        nearest = float(delays[int(np.argmin(np.abs(delays - y)))])
        spacing = float(np.min(np.diff(delays))) if len(delays) > 1 else 1.0
        if abs(nearest - y) > spacing or nearest not in cache:
            return text
        bins = cache[nearest][3]
        column = int(np.argmin(np.abs(x_values - x)))
        value = bins[column // 8] if len(bins) * 8 == len(x_values) else bins[min(column, len(bins) - 1)]
        mask = self._saturated.get(nearest)
        flag = '  ·  saturated' if mask is not None and column < mask.size and mask[column] else ''
        return f'{text}  ·  {fmt(value, 5)} counts{flag}'
    def _delay_moved(self, line):
        if self._settings:
            return
        self.delayPicked.emit(float(line.value()))
    def _wave_moved(self, line):
        if self._settings:
            return
        line.label.show();self.wavePicked.emit(float(line.value()))
    def _clicked(self, event):
        if event.button() != Qt.LeftButton or event.double():
            return
        try:
            if not self.plotItem.vb.sceneBoundingRect().contains(event.scenePos()):
                return
            point = self.plotItem.vb.mapSceneToView(event.scenePos())
            self.wavePicked.emit(float(point.x()))
            self.delayPicked.emit(float(point.y()))
        except Exception:
            pass
    def set_marker(self, delay=None, wave=None, show_delay=False):
        self._settings = True
        try:
            has_delay = delay is not None and math.isfinite(float(delay))
            if has_delay:
                self.delay_line.setPos(float(delay))
            if wave is not None and math.isfinite(float(wave)):
                self.wave_line.setPos(float(wave))
                self.wave_line.setVisible(True)
            else:
                self.wave_line.setVisible(False)
        finally:
            self._settings = False
        # The delay line marks the spectrum picked for display; while scanning it stays hidden.
        self.delay_line.setVisible(bool(show_delay and has_delay))
        if self.delay_line.isVisible():
            self.delay_line.label.valueChanged()  # pyqtgraph skips label text updates while the line is hidden
    def auto_levels(self):
        # Data-driven colour range in counts from the last set_map (what the key shows in auto).
        return getattr(self, '_auto_levels', (0.0, 1.0))
    def set_map(self, x_axis, spectra, scale='log', planned_delays=None, saturated=None, levels=None):
        # saturated: optional {delay: bool mask per CCD pixel}; those pixels are painted SATURATED_COLOR.
        # levels: optional (low, high) counts overriding the colour range; None at an end keeps it automatic.
        self.set_empty('No acquired data' if not spectra else '')
        self._saturated={delay:np.asarray(mask,dtype=bool) for delay,mask in (saturated or {}).items() if mask is not None and np.any(mask)}
        planned=sorted(set(float(d) for d in (planned_delays or [])).union(spectra))
        if not planned:
            self.image_item.clear();self._delays=[];self.saturated_item.setVisible(False)
            self.set_axis_bounds('x',0,1024);self.set_axis_bounds('y',0,1)
            self._value_cache={}
            return 0.0,0.0
        self._delays=sorted(spectra)
        width=len(next(iter(spectra.values()))) if spectra else len(x_axis or []) or 1024
        x_values=np.asarray(x_axis if x_axis is not None and len(x_axis)==width else np.arange(width),dtype=float)
        self._x_axis=x_values if x_axis is not None else None
        self._readout_x=x_values
        self.set_x_label('Wavelength / nm' if x_axis is not None else 'Pixel')
        y_min,y_max=planned[0],planned[-1]
        if y_min==y_max:y_min-=.5;y_max+=.5
        x_min,x_max=float(x_values.min()),float(x_values.max())
        if x_min==x_max:x_max=x_min+1
        cache=getattr(self,'_value_cache',{})
        updated={}
        for delay,array in spectra.items():
            previous=cache.get(delay)
            if previous is not None and previous[0] is array:
                updated[delay]=previous
            else:
                samples=np.asarray(array,dtype=float)
                bins=samples.reshape(-1,8).mean(axis=1) if samples.size%8==0 else samples
                finite=bins[np.isfinite(bins)]
                updated[delay]=(array,float(finite.min()) if finite.size else 0.0,float(finite.max()) if finite.size else 0.0,bins)
        self._value_cache=updated
        raw_min=min((entry[1] for entry in updated.values()),default=0.0)
        raw_max=max((entry[2] for entry in updated.values()),default=1.0)
        auto_low=1.0 if scale=='log' else min(0.0,raw_min)
        self._auto_levels=(auto_low,raw_max)
        shown_low,shown_high=[auto if value is None else value for value,auto in zip(levels or (None,None),self._auto_levels)]
        if scale=='log':shown_low=max(1.0,shown_low)
        low=math.log10(shown_low) if scale=='log' else shown_low
        high=math.log10(max(2.0,shown_high)) if scale=='log' else shown_high
        if high<=low:high=low+1.0
        rows=min(512,max(128,len(planned)*4));cols=min(1024,width)
        rgba=np.zeros((rows,cols,4),dtype=np.uint8)
        centers=np.asarray(planned)
        y_samples=y_min+(np.arange(rows)+.5)*(y_max-y_min)/rows
        row_indices=np.searchsorted((centers[:-1]+centers[1:])/2,y_samples)
        descending=x_values[0]>x_values[-1]
        sorted_x=x_values[::-1] if descending else x_values
        x_samples=x_min+(np.arange(cols)+.5)*(x_max-x_min)/cols
        column_indices=np.searchsorted((sorted_x[:-1]+sorted_x[1:])/2,x_samples)
        if descending:column_indices=width-1-column_indices
        lut=pg.ColorMap([0,1/3,2/3,1],map_qcolors()).getLookupTable(0,1,256)
        for index in np.unique(row_indices):
            delay=float(centers[index])
            if delay not in spectra:continue
            bins=updated[delay][3]
            values=bins[column_indices//8] if len(bins)*8==width else bins[column_indices]
            if scale=='log':values=np.log10(np.maximum(values,1.0))
            # Undefined pixels (no intensity response there) take the bottom of the colour range.
            values=np.where(np.isfinite(values),values,low)
            indices=np.rint(np.clip((values-low)/(high-low),0,1)*255).astype(np.int32)
            pixels=lut[indices,:3]
            selected=row_indices==index
            rgba[selected,:,:3]=pixels;rgba[selected,:,3]=255
        self.image_item.setImage(rgba,autoLevels=False)
        self.image_item.setRect(QRectF(x_min,y_min,x_max-x_min,y_max-y_min))
        self._draw_saturated(x_values,centers,y_min,y_max)
        self.set_axis_bounds('x',x_min,x_max);self.set_axis_bounds('y',y_min,y_max)
        return shown_low,shown_high
    def _draw_saturated(self,x_values,centers,y_min,y_max):
        # Vector overlay rather than image pixels: a dense delay row can be thinner than one screen
        # pixel, and the cosmetic pen keeps every saturated cell at least ~2 px wide at any zoom.
        path=QPainterPath()
        edges=np.concatenate(([y_min],(centers[:-1]+centers[1:])/2,[y_max]))
        x_edges=np.concatenate(([x_values[0]],(x_values[:-1]+x_values[1:])/2,[x_values[-1]]))
        for delay,mask in self._saturated.items():
            if mask.size!=x_values.size:continue
            row=int(np.searchsorted(centers,delay))
            if row>=centers.size or centers[row]!=delay:continue
            padded=np.concatenate(([False],mask,[False])).astype(np.int8)
            starts=np.flatnonzero(np.diff(padded)==1);ends=np.flatnonzero(np.diff(padded)==-1)
            for start,end in zip(starts,ends):
                left,right=sorted((float(x_edges[start]),float(x_edges[end])))
                path.addRect(QRectF(left,float(edges[row]),right-left,float(edges[row+1]-edges[row])))
        self.saturated_item.setPath(path)
        self.saturated_item.setVisible(not path.isEmpty())

class DecayPlot(EditablePlot):
    delayPicked = Signal(float)
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True, invert_y=True)
        self.set_plot_margins(10,14,10,46);self.left_axis.setStyle(showValues=False);self.left_axis.setLabel('');self.bottom_axis.divisions=2
        # Counts are the horizontal coordinate and are not monotonic in delay.
        # pyqtgraph's x-sorted clipping/downsampling would discard decay points.
        self.plotItem.setClipToView(False);self.plotItem.setDownsampling(auto=False)
        self._settings = False
        self._delays = []
        self._intensities = []
        self._items = []
        colors = palette()
        self.marker = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
        self.plotItem.addItem(self.marker)
        self.marker.sigPositionChanged.connect(self._moved)
        self.point = pg.ScatterPlotItem(size=7, brush=pg.mkBrush(colors['cursor']), pen=pg.mkPen(colors['plot_border']))
        self.plotItem.addItem(self.point)
        try:
            self.scene().sigMouseClicked.connect(self._clicked)
        except Exception:
            pass
        self.set_x_label('counts / pixel')
        self.set_y_label('')
        self.update_theme()
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'marker'):
            colors = palette()
            self.marker.setPen(pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
            self.point.setBrush(pg.mkBrush(colors['cursor']))
    def readout_text(self, x, y):
        # The decay is read by delay: report the measured point nearest the cursor row, for every decay shown.
        if not self._delays:
            return ''
        index = min(range(len(self._delays)), key=lambda i: abs(self._delays[i] - y))
        own = getattr(self, '_label', None)
        text = f'{format_reading(self._delays[index], "Delay / ps")}  ·  ' + (f'{own} ' if own else '') + format_reading(self._intensities[index], self.bottom_axis.labelText)
        for key, label, delays, values in getattr(self, '_extra', []):
            if self.series_hidden(key) or not len(delays):continue
            near = int(np.argmin(np.abs(np.asarray(delays) - self._delays[index])))
            text += f'  ·  {label} {format_reading(values[near], self.bottom_axis.labelText)}'
        return text
    def _moved(self, line):
        if not self._settings:
            self.delayPicked.emit(float(line.value()))
    def _clicked(self, event):
        if event.button() != Qt.LeftButton or event.double():
            return
        try:
            if self.plotItem.vb.sceneBoundingRect().contains(event.scenePos()):
                point = self.plotItem.vb.mapSceneToView(event.scenePos())
                self.delayPicked.emit(float(point.y()))
        except Exception:
            pass
    def set_decay(self, delays, intensities, scale='linear', key=None, extra=None, normalize=False, label=None, baseline=None):
        '''The decay at the marked wavelength (legend key `key`), and extra decays [(key, label, palette colour
        key, delays, values, baseline)] at pinned wavelengths. normalize scales each as (I - B) / (max - B) with its
        own baseline B, so every decay runs from 0 before time zero to 1 at its peak and the shapes compare.'''
        self.set_empty('No data' if not delays else '')
        self._label = label
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
        def scaled(values, base):
            values = np.asarray(values, dtype=float)
            if not normalize or not values.size:
                return values
            base = float(base) if base is not None and math.isfinite(float(base)) else 0.0
            span = float(np.nanmax(values)) - base
            return (values - base) / span if math.isfinite(span) and span > 0 else values
        pairs = []
        for delay, intensity in zip(delays, scaled(intensities, baseline)):
            if math.isfinite(float(delay)) and math.isfinite(float(intensity)):
                pairs.append((float(intensity), float(delay)))
        self._delays = [item[1] for item in pairs]
        self._intensities = [item[0] for item in pairs]
        self.set_x_label('normalized' if normalize else 'counts / pixel')
        colors = palette(); self._extra = []; spread = []
        for extra_key, label, color, extra_delays, values, extra_baseline in extra or []:
            ex = scaled(values, extra_baseline); ey = np.asarray(extra_delays, dtype=float); keep = np.isfinite(ex) & np.isfinite(ey)
            if not keep.any():continue
            item = self.plotItem.plot(ex[keep], ey[keep], antialias=True, pen=pg.mkPen(colors[color], width=1.3), symbol='o', symbolSize=4,
                                      symbolBrush=pg.mkBrush(colors[color]), symbolPen=pg.mkPen(colors[color]))
            self.tag_series(item, extra_key); self._items.append(item)
            self._extra.append((extra_key, label, ey[keep].tolist(), ex[keep].tolist())); spread += ex[keep].tolist()
        if not pairs:
            self.set_axis_bounds('x', 0, 1.1)
            self.set_axis_bounds('y', 0, 1)
            self.point.setData([], [])
            return
        colors = palette()
        xs = np.asarray([item[0] for item in pairs], dtype=float)
        ys = np.asarray([item[1] for item in pairs], dtype=float)
        item = self.plotItem.plot(xs, ys, antialias=True,pen=pg.mkPen(colors['data'], width=1.4), symbol='o', symbolSize=6, symbolBrush=pg.mkBrush(colors['data']), symbolPen=pg.mkPen(colors['data']))
        if key:self.tag_series(item, key)
        self.make_pickable(item, None, 'Delay / ps', key='y')
        self._items.append(item)
        self.set_axis_bounds('y', min(ys), max(ys))
        try:
            values = np.concatenate([xs, np.asarray(spread, dtype=float)])
            low, high = padded_range(values)
            self.set_axis_bounds('x', low, max(high, 1.05) if normalize else high)
        except Exception:
            pass
    def set_marker(self, delay=None):
        self._settings = True
        try:
            if delay is None or not self._delays:
                self.marker.setVisible(False)
                self.point.setData([], [])
                return
            value = float(delay)
            self.marker.setPos(value)
            self.marker.setVisible(True)
            nearest = min(range(len(self._delays)), key=lambda index: abs(self._delays[index] - value))
            self.point.setData([self._intensities[nearest]], [self._delays[nearest]])
        finally:
            self._settings = False

class AnalysisCurvePlot(EditablePlot):
    delayPicked = Signal(float)
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True)
        self.set_plot_margins(68,14,10,46)
        self._settings = False
        self._mode = 'decay'
        self._observed = []
        self._items = []
        colors = palette()
        self.marker = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
        self.point = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(colors['cursor']), pen=pg.mkPen(colors['plot_border']))
        self.plotItem.addItem(self.marker)
        self.plotItem.addItem(self.point)
        self.marker.sigPositionChanged.connect(self._moved)
        # Clicking an observed point also moves the delay cursor to it.
        self.pointPicked.connect(lambda x, y: self.delayPicked.emit(x) if self._mode == 'decay' else None)
        self.set_x_label('Delay / ps')
        self.set_y_label('Counts per second')
        self.update_theme()
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'marker'):
            colors = palette()
            self.marker.setPen(pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
            self.point.setBrush(pg.mkBrush(colors['cursor']))
    def _moved(self, line):
        if self._settings or self._mode != 'decay':
            return
        self.delayPicked.emit(float(line.value()))
    def _clear_items(self):
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
    def set_decay(self, observed, fit=None, scale='linear', rounds=None, left_out=None, unit='counts / pixel'):
        '''observed and fit as (delay, value) pairs; rounds as [(round_index, delays, values)] drawn behind the
        average, each round in its own colour; left_out as (delay, value) pairs of excluded points, drawn hollow in the warning colour.'''
        self.set_empty('Open an experiment to view its decay' if not observed else '')
        self._mode = 'decay'
        self._clear_items()
        self._observed = [(float(item[0]), float(item[1])) for item in (observed or [])]
        try:
            self.plotItem.setLogMode(x=False, y=(scale == 'log'))
        except Exception:
            pass
        colors = palette()
        extra = []
        from .theme import round_key
        for index, rx, ry in rounds or []:
            rx = np.asarray(rx, dtype=float); ry = np.asarray(ry, dtype=float)
            if scale == 'log':
                ry = np.where(ry > 0, ry, np.nan)
            color = QColor(colors[round_key(index)]); color.setAlpha(225)
            item = self.plotItem.plot(rx, ry, antialias=True, pen=pg.mkPen(color, width=1.3), symbol='o', symbolSize=4,
                                      symbolBrush=pg.mkBrush(color), symbolPen=pg.mkPen(color))
            self.tag_series(item, 'rounds', f'round{int(index)}')
            self._items.append(item); extra += [float(v) for v in ry if np.isfinite(v)]
        if left_out:
            lx = np.asarray([item[0] for item in left_out], dtype=float); ly = np.asarray([item[1] for item in left_out], dtype=float)
            if scale == 'log':
                ly = np.where(ly > 0, ly, np.nan)
            item = self.plotItem.plot(lx, ly, pen=None, symbol='o', symbolSize=8, symbolBrush=pg.mkBrush(0, 0, 0, 0),
                                      symbolPen=pg.mkPen(colors['warning'], width=1.6))
            self.tag_series(item, 'left_out')
            self._items.append(item); extra += [float(v) for v in ly if np.isfinite(v)]
        if self._observed:
            xs = np.asarray([item[0] for item in self._observed], dtype=float)
            ys = np.asarray([item[1] for item in self._observed], dtype=float)
            if scale == 'log':
                ys = np.where(ys > 0, ys, np.nan)
            item = self.plotItem.plot(xs, ys, antialias=True,pen=None, symbol='o', symbolSize=7, symbolBrush=pg.mkBrush(colors['data']), symbolPen=pg.mkPen(colors['data']))
            self.tag_series(item, 'observed')
            self.make_pickable(item)
            self._items.append(item)
            self.set_axis_bounds('x', min(xs), max(xs))
        else:
            self.set_axis_bounds('x', -2, 20)
        fit_points = [(float(item[0]), float(item[1])) for item in (fit or [])]
        if fit_points:
            fx = np.asarray([item[0] for item in fit_points], dtype=float)
            fy = np.asarray([item[1] for item in fit_points], dtype=float)
            if scale == 'log':
                fy = np.where(fy > 0, fy, np.nan)
            item = self.plotItem.plot(fx, fy, antialias=True,pen=pg.mkPen(colors['amber'], width=1.8))
            self.tag_series(item, 'fit')
            self._items.append(item)
        self.marker.setVisible(bool(self._observed))
        self.point.setVisible(False)
        self.marker.setVisible(False)
        self.set_x_label('Delay / ps')
        self.set_y_label(unit)
        limits=[v for _,v in self._observed]+[v for _,v in fit_points]+extra
        if scale=='log':
            limits=[math.log10(v) for v in limits if v>0]
            low=math.floor(min(limits)) if limits else 0;high=max(limits,default=1)
            if high<=low:high=low+1
            self.set_axis_bounds('y',low,high+(high-low)*.1)
        else:
            self.set_axis_bounds('y',*padded_range(limits))
        if self._observed:
            self.set_marker(self._observed[0][0])
        else:
            self.point.setData([], [])
    def set_spectra(self, curves, x_label='Wavelength / nm', scale='linear'):
        self._mode = 'spectrum'
        self._clear_items()
        self._observed = []
        self.marker.setVisible(False)
        self.point.setVisible(False)
        try:
            self.plotItem.setLogMode(x=False, y=(scale == 'log'))
        except Exception:
            pass
        xmin = None
        xmax = None
        values=[]
        for curve in curves or []:
            if not curve:
                continue
            x, y, color = curve[0], curve[1], curve[2]
            try:
                xa = np.asarray(x, dtype=float)
                ya = np.asarray(y, dtype=float)
            except Exception:
                continue
            if xa.size == 0 or ya.size == 0:
                continue
            values.extend(ya[np.isfinite(ya)].tolist())
            if scale == 'log':
                ya = np.where(ya > 0, ya, np.nan)
            item = self.plotItem.plot(xa, ya, antialias=True,pen=pg.mkPen(color, width=1.7))
            self._items.append(item)
            xmin = float(np.nanmin(xa)) if xmin is None else min(xmin, float(np.nanmin(xa)))
            xmax = float(np.nanmax(xa)) if xmax is None else max(xmax, float(np.nanmax(xa)))
        self.set_x_label(x_label)
        self.set_y_label('counts / s')
        if xmin is None or xmax is None or xmax <= xmin:
            self.set_axis_bounds('x',500,800)
        else:
            self.set_axis_bounds('x', xmin, xmax)
        try:
            if scale=='log':
                valid=[math.log10(v) for v in values if v>0]
                low=math.floor(min(valid)) if valid else 0
                high=max(valid,default=1);self.set_axis_bounds('y',low,max(low+1,high+(high-low)*.1))
            else:
                self.set_axis_bounds('y',*padded_range(values))
        except Exception:
            pass
    def set_marker(self, delay=None):
        self._settings = True
        try:
            if delay is None or not self._observed or self._mode != 'decay':
                self.point.setData([], [])
                return
            value = float(delay)
            self.marker.setPos(value)
            nearest = min(self._observed, key=lambda item: abs(item[0] - value))
            self.point.setData([nearest[0]], [nearest[1]])
        finally:
            self._settings = False

class ResidualPlot(EditablePlot):
    delayPicked = Signal(float)
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True)
        self.set_plot_margins(68,14,6,10);self.left_axis.divisions=2
        self._items = []
        self._settings = False
        colors = palette()
        self.zero = pg.InfiniteLine(pos=0, angle=0, movable=False, pen=pg.mkPen(colors['plot_border'], width=1))
        self.marker = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
        self.plotItem.addItem(self.zero)
        self.plotItem.addItem(self.marker)
        self.marker.sigPositionChanged.connect(self._moved)
        self.set_x_label('Delay / ps')
        self.set_y_label('Residual')
        self.update_theme()
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'zero'):
            colors = palette()
            self.zero.setPen(pg.mkPen(colors['plot_border'], width=1))
            self.marker.setPen(pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
    def _moved(self, line):
        if not self._settings:
            self.delayPicked.emit(float(line.value()))
    def set_points(self, points):
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
        self.set_empty('Residuals appear after fitting' if not points else '')
        data = [(float(item[0]), float(item[1])) for item in (points or [])]
        colors = palette()
        if data:
            xs = np.asarray([item[0] for item in data], dtype=float)
            ys = np.asarray([item[1] for item in data], dtype=float)
            item = self.plotItem.plot(xs, ys, antialias=True,pen=None, symbol='o', symbolSize=6, symbolBrush=pg.mkBrush(colors['data']), symbolPen=pg.mkPen(colors['amber']))
            # The x title is hidden under the shared decay plot, so name the coordinates here.
            self.make_pickable(item, 'Delay / ps', 'Residual')
            self._items.append(item)
            self.set_axis_bounds('x', min(xs), max(xs))
        else:
            self.set_axis_bounds('x', 0, 1)
        span=max(1,max((abs(item[1]) for item in data),default=1))*1.15
        self.set_axis_bounds('y', -span, span)
        self.marker.setVisible(bool(data))
    def set_marker(self, delay=None):
        self._settings = True
        try:
            if delay is None:
                self.marker.setVisible(False)
            else:
                self.marker.setPos(float(delay))
                self.marker.setVisible(True)
        finally:
            self._settings = False

class CalibrationImagePlot(EditablePlot):
    rowPicked = Signal(int)
    roiChanged = Signal(int, int)
    def x_unit(self):
        # Columns of the CCD frame: zoom-synchronised with the extracted spectrum while it is shown in pixels.
        return 'CCD pixel'
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=False, invert_y=True)
        self.clamp_to_data = True
        self.set_plot_margins(68,14,10,30);self.left_axis.divisions=3
        self._rows = 255
        self._cols = 1024
        self._region_guard = False
        self.image_item = pg.ImageItem(axisOrder='row-major')
        self.plotItem.addItem(self.image_item)
        orientation = getattr(pg.LinearRegionItem, 'Horizontal', None)
        if orientation is None:
            orientation = 1
        self.region = pg.LinearRegionItem(values=(85, 165), orientation=orientation, movable=True, brush=pg.mkBrush(50, 130, 210, 45), pen=pg.mkPen(palette()['amber'], width=1))
        self.region.setZValue(20)
        self.region.setVisible(False)
        self.plotItem.addItem(self.region)
        self.region.sigRegionChanged.connect(self._region_moved)
        try:
            self.scene().sigMouseClicked.connect(self._clicked)
        except Exception:
            pass
        self.set_x_label('')
        self.set_y_label('CCD row')
        self._full_scale = None
        self._set_lookup(65535)
        self.region.setBrush(pg.mkBrush(50,107,186,22));self.set_empty('No frame')
        self.bottom_axis.integer_ticks=True  # unlabelled CCD columns
        self.update_theme()
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'region'):
            for line in self.region.lines:
                line.setPen(pg.mkPen(palette()['cursor'],width=1,style=Qt.DashLine))
    def _region_moved(self):
        if self._region_guard:
            return
        values = sorted(self.region.getRegion())
        start = max(0, min(self._rows - 1, int(round(values[0]))))
        end = max(0, min(self._rows - 1, int(round(values[1]))))
        if end < start:
            start, end = end, start
        self.roiChanged.emit(start, end)
    def _clicked(self, event):
        if event.button() != Qt.LeftButton or event.double() or self._rows <= 1:
            return
        try:
            if self.plotItem.vb.sceneBoundingRect().contains(event.scenePos()):
                point = self.plotItem.vb.mapSceneToView(event.scenePos())
                self.rowPicked.emit(int(round(point.y())))
        except Exception:
            pass
    def set_roi(self, start, end):
        self._region_guard = True
        try:
            self.region.setRegion((float(start), float(end)))
        finally:
            self._region_guard = False
    def readout_text(self, x, y):
        text = super().readout_text(x, y)
        image = self.image_item.image
        if image is None or image.ndim != 2:
            return text
        row = 0 if self._rows == 1 else int(round(y)); column = int(round(x))
        if not (0 <= row < image.shape[0] and 0 <= column < image.shape[1]):
            return text
        value = int(image[row, column])
        flag = '  ·  saturated' if self._full_scale is not None and value >= self._full_scale else ''
        return f'{text}  ·  {value} counts{flag}'
    def _set_lookup(self, full_scale):
        # One LUT entry per count (levels 0..65535); entries at or above full scale take the saturation colour.
        full_scale = int(max(1, min(65535, round(full_scale))))
        if full_scale == self._full_scale:
            return
        values=np.arange(65536,dtype=float);f=np.clip(np.maximum(0,values-55)/3300,0,1)**.55
        lut=np.rint(np.column_stack((16+f*174,27+f*208,47+f*202))).astype(np.uint8)
        lut[full_scale:]=saturated_rgb()
        self.image_item.setLookupTable(lut)
        self._full_scale = full_scale
    def set_frame(self, image, full_scale=65535):
        self.set_empty('No frame' if image is None else '')
        if image is None:
            self.image_item.clear()
            self.region.setVisible(False)
            self.set_axis_bounds('x', 0, 1023)
            self.set_axis_bounds('y', 0, 254)
            return
        array = np.asarray(image)
        if array.ndim != 2:
            self.image_item.clear()
            return
        self._rows, self._cols = array.shape
        self._set_lookup(full_scale)
        self.image_item.setImage(array, autoLevels=False,levels=(0,65535))
        self.image_item.setRect(QRectF(0,-.5 if self._rows==1 else 0,self._cols-1,1 if self._rows==1 else self._rows-1))
        self.set_axis_bounds('x', 0, self._cols-1)
        self.set_axis_bounds('y', -.5 if self._rows==1 else 0, .5 if self._rows==1 else self._rows-1)
        self.region.setVisible(self._rows > 1)

class CalibrationProfilePlot(EditablePlot):
    pixelPicked = Signal(int)
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True)
        self.set_plot_margins(68,14,10,46);self.left_axis.divisions=3
        self._items = []
        self._x_values = None
        try:
            self.scene().sigMouseClicked.connect(self._clicked)
        except Exception:
            pass
        self.set_x_label('CCD pixel')
        self.set_y_label('Mean counts')
        self.update_theme()
    def _clicked(self, event):
        if event.button() != Qt.LeftButton or event.double():
            return
        try:
            if not self.plotItem.vb.sceneBoundingRect().contains(event.scenePos()):
                return
            point = self.plotItem.vb.mapSceneToView(event.scenePos())
            x = float(point.x())
            if self._x_values is not None and len(self._x_values) > 1:
                index = int(np.argmin(np.abs(np.asarray(self._x_values) - x)))
            else:
                index = int(round(x))
            self.pixelPicked.emit(index)
        except Exception:
            pass
    def set_profile(self, profile, x_values=None, axis_mode='pixel', references=None):
        self.set_empty('No spectrum' if profile is None else '')
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
        colors = palette()
        if profile is None or len(profile) == 0:
            self._x_values = None
            self.set_axis_bounds('x', 0, 1023)
            self.set_axis_bounds('y',0,65535)
            return
        values = np.asarray(profile, dtype=float)
        if x_values is not None and len(x_values) == len(values):
            x = np.asarray(x_values, dtype=float)
            self._x_values = x
        else:
            x = np.arange(len(values), dtype=float)
            self._x_values = x
        item = self.plotItem.plot(x, values, antialias=True,pen=pg.mkPen(colors['data'], width=1.6))
        self.tag_series(item, 'ccd')
        self._items.append(item)
        for index,reference in enumerate(references or []):
            try:
                line = pg.InfiniteLine(pos=float(reference), angle=90, movable=False, pen=pg.mkPen(colors['amber'], width=1, style=Qt.DashLine))
                self.plotItem.addItem(line); self.tag_series(line, 'references')
                self._items.append(line)
                label=pg.TextItem('L'+str(index+1),color=colors['muted'],anchor=(.5,0));font=QFont('Segoe UI');font.setPixelSize(11);label.setFont(font);label.setPos(float(reference),padded_range(values, high_pad=.12)[1]);self.plotItem.addItem(label);self.tag_series(label,'references');self._items.append(label)
                nearest=int(np.argmin(np.abs(x-float(reference))));point=self.plotItem.plot([x[nearest]],[values[nearest]],antialias=True,pen=None,symbol='o',symbolSize=5,symbolBrush=colors['data'],symbolPen=None);self.tag_series(point,'references');self._items.append(point)
            except Exception:
                pass
        self.set_x_label('')
        self.set_y_label('CCD / counts')
        self.set_axis_bounds('x', float(np.nanmin(x)), float(np.nanmax(x)))
        try:
            # Room above the spectrum for the reference-line labels.
            self.set_axis_bounds('y',*padded_range(values, high_pad=.2))
        except Exception:
            pass

class ResponsePlot(EditablePlot):
    '''The intensity response R(lambda): the installed one solid, a preview dashed.'''
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True)
        # Tick labels and the wavelength title need 46 px, plus a small bottom gutter.
        self.set_plot_margins(40, 10, 8, 50); self.left_axis.divisions = 2; self.bottom_axis.divisions = 3
        self._items = []; self.set_x_label('Wavelength / nm'); self.set_y_label('R')
        self.set_curves()
    def set_curves(self, installed=None, preview=None):
        for item in self._items:
            self.plotItem.removeItem(item)
        self._items = []; colors = palette(); xs = []
        for curve, pen in ((installed, pg.mkPen(colors['data'], width=1.6)), (preview, pg.mkPen(colors['amber'], width=1.6, style=Qt.DashLine))):
            if curve is None:
                continue
            x = np.asarray(curve[0], dtype=float); y = np.asarray(curve[1], dtype=float)
            self._items.append(self.plotItem.plot(x, y, antialias=True, pen=pen)); xs += [float(x.min()), float(x.max())]
        self.set_empty('' if xs else 'No intensity response')
        self.set_axis_bounds('x', min(xs) if xs else 500.0, max(xs) if xs else 800.0)
        self.set_axis_bounds('y', 0.0, 1.08)

class CalibrationMappingPlot(EditablePlot):
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True)
        self.set_plot_margins(62,14,10,30);self.left_axis.divisions=2
        self._items = []
        self.set_x_label('')
        self.set_y_label('λ / nm')
        self.update_theme()
    def set_data(self, lines, solution=None):
        self.set_empty('No mapping' if solution is None else '')
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
        colors = palette()
        pixels = []
        wavelengths = []
        for line in lines or []:
            if not line.get('enabled'):
                continue
            pixel = line.get('pixel')
            wavelength = line.get('wavelength_nm')
            if pixel is None or wavelength is None:
                continue
            pixels.append(float(pixel))
            wavelengths.append(float(wavelength))
        if pixels:
            item = self.plotItem.plot(np.asarray(pixels), np.asarray(wavelengths), antialias=True,pen=None, symbol='o', symbolSize=7, symbolBrush=pg.mkBrush(colors['data']), symbolPen=pg.mkPen(colors['data']))
            self.make_pickable(item, 'CCD pixel', 'λ / nm')
            self._items.append(item)
        if solution and solution.get('wavelengths_nm'):
            axis = np.asarray(solution.get('wavelengths_nm'), dtype=float)
            if axis.size:
                item = self.plotItem.plot(np.arange(axis.size), axis, antialias=True,pen=pg.mkPen(colors['amber'], width=1.5))
                self._items.append(item)
                if not pixels:
                    self.set_axis_bounds('y', float(np.nanmin(axis)), float(np.nanmax(axis)))
        if pixels:
            self.set_axis_bounds('x', 0,1023)
            self.set_axis_bounds('y', min(wavelengths)-15,max(wavelengths)+15)
        elif not solution:
            self.set_axis_bounds('x', 0, 1023)
            self.set_axis_bounds('y',490,770)

class CalibrationResidualPlot(EditablePlot):
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True)
        self.set_plot_margins(62,14,8,8);self.left_axis.divisions=2
        self._items = []
        colors = palette()
        self.zero = pg.InfiniteLine(pos=0, angle=0, movable=False, pen=pg.mkPen(colors['plot_border'], width=1))
        self.plotItem.addItem(self.zero)
        self.set_x_label('CCD pixel')
        self.set_y_label('Δ / nm')
        self.update_theme()
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'zero'):
            self.zero.setPen(pg.mkPen(palette()['plot_border'], width=1))
    def set_data(self, lines, solution=None):
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
        if not solution:
            self.set_axis_bounds('x', 0, 1024)
            self.set_axis_bounds('y', -.1, .1)
            return
        coeffs = solution.get('coefficients') or []
        origin = solution.get('pixel_origin', 511.5)
        scale = solution.get('pixel_scale', 511.5)
        xs = []
        ys = []
        if coeffs:
            for line in lines or []:
                if not line.get('enabled'):
                    continue
                pixel = line.get('pixel')
                wavelength = line.get('wavelength_nm')
                if pixel is None or wavelength is None:
                    continue
                x = (float(pixel) - float(origin)) / float(scale)
                model = float(_polyval(coeffs, np.asarray([x]))[0])
                xs.append(float(pixel))
                ys.append(float(wavelength) - model)
        else:
            residuals = solution.get('residuals_nm') or []
            pairs = solution.get('line_pairs') or []
            for pair, residual in zip(pairs, residuals):
                if pair.get('enabled') and pair.get('pixel') is not None:
                    xs.append(float(pair['pixel']))
                    ys.append(float(residual))
        colors = palette()
        if xs:
            item = self.plotItem.plot(np.asarray(xs), np.asarray(ys), antialias=True,pen=None, symbol='o', symbolSize=5, symbolBrush=pg.mkBrush(colors['amber']), symbolPen=pg.mkPen(colors['amber']))
            self.make_pickable(item, 'CCD pixel', 'Δ / nm')
            self._items.append(item)
            self.set_axis_bounds('x', min(xs), max(xs))
            span = max(.002, max(abs(value) for value in ys) * 1.4)
            self.set_axis_bounds('y', -span, span)
        else:
            self.set_axis_bounds('x', 0, 1024)
            self.set_axis_bounds('y', -1, 1)
