'''Native pyqtgraph plots with inline editable axis endpoints.'''
import math
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal, QEvent, QPointF, QRectF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QLineEdit, QHBoxLayout
from .theme import palette, map_color, map_qcolors

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

def _polyval(coeffs, x):
    values = np.zeros_like(np.asarray(x, dtype=float))
    for coefficient in reversed(list(coeffs)):
        values = values * x + float(coefficient)
    return values

class InlineAxis(pg.AxisItem):
    def __init__(self, orientation, owner=None):
        super().__init__(orientation=orientation)
        self.owner = owner
    def tickValues(self,minVal,maxVal,size):
        levels=super().tickValues(minVal,maxVal,size)
        if not levels or maxVal<=minVal:return levels
        spacing,major=levels[0]
        margin=(maxVal-minVal)*.06
        return [(spacing,sorted(set([minVal,maxVal]+[value for value in major if minVal+margin<value<maxVal-margin])))]
    def mouseClickEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.owner is not None:
            try:
                if self.orientation in ('bottom', 'top'):
                    width = max(1, self.geometry().width())
                    x = ev.pos().x()
                    if x < 36 or x > width - 36:
                        self.owner.begin_axis_edit('x', 'min' if x < width / 2 else 'max')
                        ev.accept()
                        return
                else:
                    height = max(1, self.geometry().height())
                    y = ev.pos().y()
                    if y < 30 or y > height - 30:
                        if self.owner._invert_y:
                            endpoint = 'min' if y < height / 2 else 'max'
                        else:
                            endpoint = 'max' if y < height / 2 else 'min'
                        self.owner.begin_axis_edit('y', endpoint)
                        ev.accept()
                        return
            except Exception:
                pass
        super().mouseClickEvent(ev)

class EditablePlot(pg.PlotWidget):
    def __init__(self, parent=None, grid=True, invert_y=False, **kwargs):
        self._grid = bool(grid)
        self._invert_y = bool(invert_y)
        self._editor = None
        self._editor_edit = None
        self._edit_axis = None
        self._edit_endpoint = None
        self._manual_ranges = {}
        self._linked_axes={}
        bottom = InlineAxis('bottom')
        left = InlineAxis('left')
        super().__init__(parent=parent, axisItems={'bottom': bottom, 'left': left}, **kwargs)
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
        self._apply_style()
    def _apply_style(self):
        colors = palette()
        self.setBackground(colors['surface'])
        self.plotItem.vb.setBackgroundColor(colors['plot'])
        for axis in (self.bottom_axis, self.left_axis):
            axis.setPen(pg.mkPen(colors['plot_border']))
            axis.setTextPen(pg.mkPen(colors['muted']))
            try:
                axis.setStyle(tickLength=0, tickTextOffset=4,hideOverlappingLabels=False)
            except Exception:
                pass
        try:
            self.plotItem.showGrid(x=self._grid, y=self._grid, alpha=0.22 if self._grid else 0.0)
        except Exception:
            pass
    def update_theme(self):
        self._apply_style()
    def set_x_label(self, text):
        self.bottom_axis.setLabel(text, color=palette()['muted'])
    def set_y_label(self, text):
        self.left_axis.setLabel(text, color=palette()['muted'])
    def set_axis_bounds(self, axis, low, high, manual=False):
        source=self._linked_axes.get(axis)
        if source is not None:
            return source.set_axis_bounds(axis,low,high,manual=True) if manual else True
        log_mode=bool((self.bottom_axis if axis=='x' else self.left_axis).logMode)
        saved=self._manual_ranges.get(axis)
        if manual:
            self._manual_ranges[axis]=(float(low),float(high),log_mode)
        elif saved and saved[2]==log_mode:
            low,high=saved[:2]
        elif saved:
            self._manual_ranges.pop(axis,None)
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
        if axis not in self._manual_ranges and axis not in self._linked_axes:
            self.plotItem.vb.enableAutoRange(axis=pg.ViewBox.XAxis if axis=='x' else pg.ViewBox.YAxis,enable=True)
    def link_axis(self,axis,source):
        self._linked_axes[axis]=source
        if axis=='x':self.plotItem.vb.setXLink(source.plotItem.vb)
        else:self.plotItem.vb.setYLink(source.plotItem.vb)
    def begin_axis_edit(self, axis, endpoint):
        ranges = self.viewRange()
        values = sorted(ranges[0] if axis == 'x' else ranges[1])
        low, high = values[0], values[1]
        value = low if endpoint == 'min' else high
        if (self.bottom_axis if axis=='x' else self.left_axis).logMode:
            value=10.0**value
        if self._editor is None:
            self._editor = QFrame(self)
            self._editor.setObjectName('axisInlineEditor')
            layout = QHBoxLayout(self._editor)
            layout.setContentsMargins(4, 3, 4, 3)
            layout.setSpacing(0)
            self._editor_edit = QLineEdit()
            self._editor_edit.setObjectName('axisInlineValue')
            self._editor_edit.setFixedWidth(82)
            self._editor_edit.installEventFilter(self)
            layout.addWidget(self._editor_edit)
            self._editor.setFixedSize(90, 32)
        self._edit_axis = axis
        self._edit_endpoint = endpoint
        self._editor_edit.setStyleSheet('')
        self._editor_edit.setText(fmt(value, 8))
        if axis == 'x':
            x = 8 if endpoint == 'min' else max(8, self.width() - 98)
            y = max(4, self.height() - 36)
        else:
            x = 8
            if self._invert_y:
                y = 8 if endpoint == 'min' else max(8, self.height() - 42)
            else:
                y = 8 if endpoint == 'max' else max(8, self.height() - 42)
        bounds=self.plotItem.vb.sceneBoundingRect()
        if axis=='x':
            point=self.mapFromScene(QPointF(bounds.left() if endpoint=='min' else bounds.right(),bounds.bottom()))
            x=point.x()-45;y=point.y()+3
        else:
            top=(endpoint=='min') if self._invert_y else (endpoint=='max')
            point=self.mapFromScene(QPointF(bounds.left(),bounds.top() if top else bounds.bottom()))
            x=point.x()-94;y=point.y()-16
        self._editor.move(max(0,min(self.width()-90,int(x))),max(0,min(self.height()-32,int(y))))
        self._editor.show()
        self._editor.raise_()
        self._editor_edit.setFocus()
        self._editor_edit.selectAll()
    def eventFilter(self, obj, event):
        if obj is self._editor_edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._close_editor()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._commit_editor()
                return True
        return super().eventFilter(obj, event)
    def _close_editor(self):
        if self._editor is not None:
            self._editor.hide()
    def _commit_editor(self):
        try:
            value = float(self._editor_edit.text().strip())
        except Exception:
            self._editor_edit.setStyleSheet('border-color:#b3263a')
            return
        if not math.isfinite(value):
            self._editor_edit.setStyleSheet('border-color:#b3263a')
            return
        if (self.bottom_axis if self._edit_axis=='x' else self.left_axis).logMode:
            if value<=0:
                self._editor_edit.setStyleSheet('border-color:#b3263a');return
            value=math.log10(value)
        ranges = self.viewRange()
        low, high = sorted(ranges[0] if self._edit_axis == 'x' else ranges[1])
        if self._edit_endpoint == 'min':
            low = value
        else:
            high = value
        if high <= low:
            self._editor_edit.setStyleSheet('border-color:#b3263a')
            return
        self.set_axis_bounds(self._edit_axis, low, high, manual=True)
        self._close_editor()

class SpectrumPlot(EditablePlot):
    wavePicked=Signal(float)
    def __init__(self, parent=None, grid=True):
        super().__init__(parent=parent, grid=grid)
        self._items = []
        self._marker_guard=False
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
            self.wave_line.setVisible(visible);self.band.setVisible(visible)
        finally:self._marker_guard=False
    def _clear(self):
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
    def set_spectra(self, curves, x_label='Wavelength / nm', y_label='Counts', scale='linear'):
        self._clear()
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
            item = self.plotItem.plot(xa, ya, pen=pen)
            self._items.append(item)
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
            curves.append((x[:len(background)], background, colors['amber'], True, 1.4))
        if current is not None and len(current):
            curves.append((x[:len(current)], current, colors['data'], False, 1.7))
        self.set_spectra(curves, x_label, 'Counts', scale)

class MapPlot(EditablePlot):
    delayPicked = Signal(float)
    wavePicked = Signal(float)
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=False, invert_y=True)
        self._settings = False
        self._x_axis = None
        self._delays = []
        self.image_item = pg.ImageItem(axisOrder='row-major')
        self.image_item.setZValue(-20)
        self.plotItem.addItem(self.image_item)
        colors = palette()
        self.delay_line = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine),label='{value:.3g} ps',labelOpts={'position':.85})
        self.wave_line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine),label='{value:.2f} nm',labelOpts={'position':.95})
        self.plotItem.addItem(self.delay_line)
        self.plotItem.addItem(self.wave_line)
        self.delay_line.sigPositionChanged.connect(self._delay_moved)
        self.wave_line.sigPositionChanged.connect(self._wave_moved)
        try:
            self.scene().sigMouseClicked.connect(self._clicked)
        except Exception:
            pass
        self.set_x_label('Wavelength / nm')
        self.set_y_label('Delay / ps')
        self.update_theme()
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'delay_line'):
            colors = palette()
            self.delay_line.setPen(pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
            self.wave_line.setPen(pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
    def _delay_moved(self, line):
        if self._settings:
            return
        self.delayPicked.emit(float(line.value()))
    def _wave_moved(self, line):
        if self._settings:
            return
        self.wavePicked.emit(float(line.value()))
    def _clicked(self, event):
        if event.button() != Qt.LeftButton:
            return
        try:
            if not self.plotItem.sceneBoundingRect().contains(event.scenePos()):
                return
            point = self.plotItem.vb.mapSceneToView(event.scenePos())
            self.wavePicked.emit(float(point.x()))
            self.delayPicked.emit(float(point.y()))
        except Exception:
            pass
    def set_marker(self, delay=None, wave=None):
        self._settings = True
        try:
            if delay is not None and math.isfinite(float(delay)):
                self.delay_line.setPos(float(delay))
                self.delay_line.setVisible(True)
            else:
                self.delay_line.setVisible(False)
            if wave is not None and math.isfinite(float(wave)):
                self.wave_line.setPos(float(wave))
                self.wave_line.setVisible(True)
            else:
                self.wave_line.setVisible(False)
        finally:
            self._settings = False
    def set_map(self, x_axis, spectra, scale='log', planned_delays=None):
        planned=sorted(set(float(d) for d in (planned_delays or [])).union(spectra))
        if not planned:
            self.image_item.clear();self._delays=[]
            self.set_axis_bounds('x',0,1024);self.set_axis_bounds('y',0,1)
            self._value_cache={}
            return 0.0,0.0
        self._delays=sorted(spectra)
        width=len(next(iter(spectra.values()))) if spectra else len(x_axis or []) or 1024
        x_values=np.asarray(x_axis if x_axis is not None and len(x_axis)==width else np.arange(width),dtype=float)
        self._x_axis=x_values if x_axis is not None else None
        self.set_x_label('Wavelength / nm' if x_axis is not None else 'CCD pixel')
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
                finite=np.asarray(array)[np.isfinite(array)]
                updated[delay]=(array,float(finite.min()) if finite.size else 0.0,float(finite.max()) if finite.size else 0.0)
        self._value_cache=updated
        raw_min=min((entry[1] for entry in updated.values()),default=0.0)
        raw_max=max((entry[2] for entry in updated.values()),default=1.0)
        low=math.log10(max(1.0,raw_min)) if scale=='log' else min(0.0,raw_min)
        high=math.log10(max(1.0,raw_max)) if scale=='log' else raw_max
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
        lut=pg.ColorMap([0,.333,.666,1],map_qcolors()).getLookupTable(0,1,256)
        for index in np.unique(row_indices):
            delay=float(centers[index])
            if delay not in spectra:continue
            values=np.asarray(spectra[delay],dtype=float)[column_indices]
            if scale=='log':values=np.log10(np.maximum(values,1.0))
            indices=np.rint(np.clip((values-low)/(high-low),0,1)*255).astype(np.int32)
            pixels=lut[indices,:3]
            selected=row_indices==index
            rgba[selected,:,:3]=pixels;rgba[selected,:,3]=255
        self.image_item.setImage(rgba,autoLevels=False)
        self.image_item.setRect(QRectF(x_min,y_min,x_max-x_min,y_max-y_min))
        self.set_axis_bounds('x',x_min,x_max);self.set_axis_bounds('y',y_min,y_max)
        return (max(1.0,raw_min) if scale=='log' else min(0.0,raw_min)),raw_max

class DecayPlot(EditablePlot):
    delayPicked = Signal(float)
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True, invert_y=True)
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
        self.set_x_label('Intensity / counts')
        self.set_y_label('Delay / ps')
        self.update_theme()
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'marker'):
            colors = palette()
            self.marker.setPen(pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
            self.point.setBrush(pg.mkBrush(colors['cursor']))
    def _moved(self, line):
        if not self._settings:
            self.delayPicked.emit(float(line.value()))
    def _clicked(self, event):
        if event.button() != Qt.LeftButton:
            return
        try:
            if self.plotItem.sceneBoundingRect().contains(event.scenePos()):
                point = self.plotItem.vb.mapSceneToView(event.scenePos())
                self.delayPicked.emit(float(point.y()))
        except Exception:
            pass
    def set_decay(self, delays, intensities, scale='linear'):
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
        pairs = []
        for delay, intensity in zip(delays, intensities):
            if math.isfinite(float(delay)) and math.isfinite(float(intensity)):
                pairs.append((float(intensity), float(delay)))
        self._delays = [item[1] for item in pairs]
        self._intensities = [item[0] for item in pairs]
        if not pairs:
            self.set_axis_bounds('x', 0, 1)
            self.set_axis_bounds('y', 0, 1)
            self.point.setData([], [])
            return
        colors = palette()
        xs = np.asarray([item[0] for item in pairs], dtype=float)
        ys = np.asarray([item[1] for item in pairs], dtype=float)
        item = self.plotItem.plot(xs, ys, pen=pg.mkPen(colors['data'], width=1.4), symbol='o', symbolSize=5, symbolBrush=pg.mkBrush(colors['data']), symbolPen=pg.mkPen(colors['data']))
        self._items.append(item)
        self.set_axis_bounds('y', min(ys), max(ys))
        try:
            self.auto_axis('x')
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
    def set_decay(self, observed, fit=None, scale='linear'):
        self._mode = 'decay'
        self._clear_items()
        self._observed = [(float(item[0]), float(item[1])) for item in (observed or [])]
        try:
            self.plotItem.setLogMode(x=False, y=(scale == 'log'))
        except Exception:
            pass
        colors = palette()
        if self._observed:
            xs = np.asarray([item[0] for item in self._observed], dtype=float)
            ys = np.asarray([item[1] for item in self._observed], dtype=float)
            if scale == 'log':
                ys = np.where(ys > 0, ys, np.nan)
            item = self.plotItem.plot(xs, ys, pen=None, symbol='o', symbolSize=7, symbolBrush=pg.mkBrush(colors['data']), symbolPen=pg.mkPen(colors['data']))
            item.sigPointsClicked.connect(lambda item,points,event:self.delayPicked.emit(float(points[0].pos().x())) if points else None)
            self._items.append(item)
            self.set_axis_bounds('x', min(xs), max(xs))
        else:
            self.set_axis_bounds('x', 0, 1)
        fit_points = [(float(item[0]), float(item[1])) for item in (fit or [])]
        if fit_points:
            fx = np.asarray([item[0] for item in fit_points], dtype=float)
            fy = np.asarray([item[1] for item in fit_points], dtype=float)
            if scale == 'log':
                fy = np.where(fy > 0, fy, np.nan)
            item = self.plotItem.plot(fx, fy, pen=pg.mkPen(colors['amber'], width=1.8))
            self._items.append(item)
        self.marker.setVisible(bool(self._observed))
        self.point.setVisible(bool(self._observed))
        self.set_x_label('Delay / ps')
        self.set_y_label('Counts per second')
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
            if scale == 'log':
                ya = np.where(ya > 0, ya, np.nan)
            item = self.plotItem.plot(xa, ya, pen=pg.mkPen(color, width=1.7))
            self._items.append(item)
            xmin = float(np.nanmin(xa)) if xmin is None else min(xmin, float(np.nanmin(xa)))
            xmax = float(np.nanmax(xa)) if xmax is None else max(xmax, float(np.nanmax(xa)))
        self.set_x_label(x_label)
        self.set_y_label('Normalized counts')
        if xmin is None or xmax is None or xmax <= xmin:
            self.set_axis_bounds('x', 0, 1024)
        else:
            self.set_axis_bounds('x', xmin, xmax)
        try:
            self.auto_axis('y')
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
        self._items = []
        self._settings = False
        colors = palette()
        self.zero = pg.InfiniteLine(pos=0, angle=0, movable=False, pen=pg.mkPen(colors['plot_border'], width=1))
        self.marker = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(colors['cursor'], width=1, style=Qt.DashLine))
        self.plotItem.addItem(self.zero)
        self.plotItem.addItem(self.marker)
        self.marker.sigPositionChanged.connect(self._moved)
        self.set_x_label('Delay / ps')
        self.set_y_label('Residual / counts per second')
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
        data = [(float(item[0]), float(item[1])) for item in (points or [])]
        colors = palette()
        if data:
            xs = np.asarray([item[0] for item in data], dtype=float)
            ys = np.asarray([item[1] for item in data], dtype=float)
            item = self.plotItem.plot(xs, ys, pen=None, symbol='o', symbolSize=5, symbolBrush=pg.mkBrush(colors['amber']), symbolPen=pg.mkPen(colors['amber']))
            self._items.append(item)
            self.set_axis_bounds('x', min(xs), max(xs))
        else:
            self.set_axis_bounds('x', 0, 1)
        span=max((abs(item[1]) for item in data),default=1)*1.15 or 1
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
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=False, invert_y=True)
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
        self.set_x_label('CCD column / px')
        self.set_y_label('Row')
        self.update_theme()
    def update_theme(self):
        super().update_theme()
        if hasattr(self, 'region'):
            for line in self.region.lines:
                line.setPen(pg.mkPen(palette()['amber'], width=1))
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
        if event.button() != Qt.LeftButton or self._rows <= 1:
            return
        try:
            if self.plotItem.sceneBoundingRect().contains(event.scenePos()):
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
    def set_frame(self, image):
        if image is None:
            self.image_item.clear()
            self.region.setVisible(False)
            self.set_axis_bounds('x', 0, 1024)
            self.set_axis_bounds('y', 0, 255)
            return
        array = np.asarray(image)
        if array.ndim != 2:
            self.image_item.clear()
            return
        self._rows, self._cols = array.shape
        self.image_item.setImage(array, autoLevels=True)
        self.image_item.setRect(QRectF(0, 0, self._cols, self._rows))
        self.set_axis_bounds('x', 0, self._cols)
        self.set_axis_bounds('y', 0, self._rows)
        self.region.setVisible(self._rows > 1)

class CalibrationProfilePlot(EditablePlot):
    pixelPicked = Signal(int)
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True)
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
        if event.button() != Qt.LeftButton:
            return
        try:
            if not self.plotItem.sceneBoundingRect().contains(event.scenePos()):
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
        for item in self._items:
            try:
                self.plotItem.removeItem(item)
            except Exception:
                pass
        self._items = []
        colors = palette()
        if profile is None or len(profile) == 0:
            self._x_values = None
            self.set_axis_bounds('x', 0, 1024)
            return
        values = np.asarray(profile, dtype=float)
        if x_values is not None and len(x_values) == len(values):
            x = np.asarray(x_values, dtype=float)
            self._x_values = x
        else:
            x = np.arange(len(values), dtype=float)
            self._x_values = x
        item = self.plotItem.plot(x, values, pen=pg.mkPen(colors['data'], width=1.6))
        self._items.append(item)
        for reference in references or []:
            try:
                line = pg.InfiniteLine(pos=float(reference), angle=90, movable=False, pen=pg.mkPen(colors['amber'], width=1, style=Qt.DashLine))
                self.plotItem.addItem(line)
                self._items.append(line)
            except Exception:
                pass
        self.set_x_label('Wavelength / nm' if axis_mode == 'wavelength' else 'CCD pixel')
        self.set_y_label('Mean counts')
        self.set_axis_bounds('x', float(np.nanmin(x)), float(np.nanmax(x)))
        try:
            self.auto_axis('y')
        except Exception:
            pass

class CalibrationMappingPlot(EditablePlot):
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True)
        self._items = []
        self.set_x_label('CCD pixel')
        self.set_y_label('Wavelength / nm')
        self.update_theme()
    def set_data(self, lines, solution=None):
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
            item = self.plotItem.plot(np.asarray(pixels), np.asarray(wavelengths), pen=None, symbol='o', symbolSize=7, symbolBrush=pg.mkBrush(colors['data']), symbolPen=pg.mkPen(colors['data']))
            self._items.append(item)
        if solution and solution.get('wavelengths_nm'):
            axis = np.asarray(solution.get('wavelengths_nm'), dtype=float)
            if axis.size:
                item = self.plotItem.plot(np.arange(axis.size), axis, pen=pg.mkPen(colors['amber'], width=1.5))
                self._items.append(item)
                if not pixels:
                    self.set_axis_bounds('y', float(np.nanmin(axis)), float(np.nanmax(axis)))
        if pixels:
            self.set_axis_bounds('x', min(pixels), max(pixels))
            self.set_axis_bounds('y', min(wavelengths), max(wavelengths))
        elif not solution:
            self.set_axis_bounds('x', 0, 1024)
            self.set_axis_bounds('y', 0, 1)

class CalibrationResidualPlot(EditablePlot):
    def __init__(self, parent=None):
        super().__init__(parent=parent, grid=True)
        self._items = []
        colors = palette()
        self.zero = pg.InfiniteLine(pos=0, angle=0, movable=False, pen=pg.mkPen(colors['plot_border'], width=1))
        self.plotItem.addItem(self.zero)
        self.set_x_label('CCD pixel')
        self.set_y_label('Residual / nm')
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
            self.set_axis_bounds('y', -1, 1)
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
            item = self.plotItem.plot(np.asarray(xs), np.asarray(ys), pen=None, symbol='o', symbolSize=5, symbolBrush=pg.mkBrush(colors['amber']), symbolPen=pg.mkPen(colors['amber']))
            self._items.append(item)
            self.set_axis_bounds('x', min(xs), max(xs))
            span = max(1e-9, max(abs(value) for value in ys) * 1.2)
            self.set_axis_bounds('y', -span, span)
        else:
            self.set_axis_bounds('x', 0, 1024)
            self.set_axis_bounds('y', -1, 1)
