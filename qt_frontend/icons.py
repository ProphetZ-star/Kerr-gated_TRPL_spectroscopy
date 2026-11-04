'''Line icons drawn on a 24-unit grid with 2-unit round strokes, in the spirit of Lucide.

Painted rather than loaded so they follow the theme colours and need no asset files. Every glyph keeps
the same optical box (roughly 4–20 on the grid), so an icon sits level with the button text beside it.'''
from PySide6.QtCore import Qt, QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# Button text is centred on its line box, which puts the letters' optical centre about 2 px below the
# box centre; glyphs are lowered by the same amount so they sit level with the words beside them.
TEXT_DROP = 2


def icon_box(size, drop=TEXT_DROP):
    '''Icon size to set on a button for line_icon(size=size, drop=drop): the canvas grows to keep the glyph unclipped.'''
    return QSize(size, size + 2 * drop)


def _poly(path, *points, close=False):
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    if close:
        path.closeSubpath()


def _shape(name):
    '''(stroke path, fill path or None) on the 24-unit grid.'''
    s = QPainterPath(); f = None
    if name == 'play':
        _poly(s, (7, 4.5), (19, 12), (7, 19.5), close=True)
    elif name == 'play-fill':
        f = QPainterPath(); _poly(f, (7, 4.5), (19, 12), (7, 19.5), close=True); s = f
    elif name == 'pause':
        s.addRoundedRect(QRectF(6.5, 5, 3.5, 14), 1, 1); s.addRoundedRect(QRectF(14, 5, 3.5, 14), 1, 1)
    elif name == 'pause-fill':
        f = QPainterPath(); f.addRoundedRect(QRectF(6.5, 5, 3.5, 14), 1, 1); f.addRoundedRect(QRectF(14, 5, 3.5, 14), 1, 1); s = f
    elif name == 'stop':
        s.addRoundedRect(QRectF(5.5, 5.5, 13, 13), 2, 2)
    elif name == 'stop-fill':
        f = QPainterPath(); f.addRoundedRect(QRectF(5.5, 5.5, 13, 13), 2, 2); s = f
    elif name == 'flag':
        _poly(s, (5, 21), (5, 4)); _poly(s, (5, 4.5), (18, 4.5), (15, 9), (18, 13.5), (5, 13.5))
    elif name == 'target':
        s.addEllipse(QPointF(12, 12), 8, 8); s.addEllipse(QPointF(12, 12), 3, 3)
        for a, b in (((12, 2), (12, 5)), ((12, 19), (12, 22)), ((2, 12), (5, 12)), ((19, 12), (22, 12))):
            _poly(s, a, b)
    elif name == 'home':
        _poly(s, (3, 11), (12, 3.5), (21, 11)); _poly(s, (5.5, 9.5), (5.5, 20), (18.5, 20), (18.5, 9.5)); _poly(s, (10, 20), (10, 14), (14, 14), (14, 20))
    elif name == 'folder':
        _poly(s, (3, 6.5), (9.5, 6.5), (11.5, 8.5), (21, 8.5), (21, 19), (3, 19), close=True)
    elif name == 'download':
        _poly(s, (12, 3.5), (12, 15)); _poly(s, (7, 10), (12, 15), (17, 10)); _poly(s, (4, 16.5), (4, 20), (20, 20), (20, 16.5))
    elif name == 'activity':
        _poly(s, (2.5, 12), (6.5, 12), (9.5, 4.5), (14.5, 19.5), (17.5, 12), (21.5, 12))
    elif name == 'layers':
        _poly(s, (12, 3.5), (21, 8.5), (12, 13.5), (3, 8.5), close=True); _poly(s, (3, 13.5), (12, 18.5), (21, 13.5))
    elif name == 'plus':
        _poly(s, (12, 5), (12, 19)); _poly(s, (5, 12), (19, 12))
    elif name == 'info':
        s.addEllipse(QPointF(12, 12), 9, 9); _poly(s, (12, 11), (12, 16.5)); s.addEllipse(QPointF(12, 7.6), .45, .45)
    elif name == 'check':
        _poly(s, (4.5, 12.5), (9.5, 17.5), (19.5, 6.5))
    elif name == 'arrow-right':
        _poly(s, (4, 12), (20, 12)); _poly(s, (14, 6), (20, 12), (14, 18))
    elif name == 'trash':
        _poly(s, (4, 7), (20, 7)); _poly(s, (9, 7), (9, 4.5), (15, 4.5), (15, 7)); _poly(s, (6, 7), (7, 20), (17, 20), (18, 7))
        _poly(s, (10, 11), (10, 16.5)); _poly(s, (14, 11), (14, 16.5))
    elif name == 'save':
        _poly(s, (4, 4), (17, 4), (20, 7), (20, 20), (4, 20), close=True); _poly(s, (8, 4), (8, 9), (15, 9), (15, 4)); _poly(s, (8, 20), (8, 14), (16, 14), (16, 20))
    else:
        raise KeyError(name)
    return s, f


_CACHE = {}
def line_icon(name, color, disabled_color=None, size=16, drop=TEXT_DROP):
    key = (name, color, disabled_color, size, drop)
    if key in _CACHE:
        return _CACHE[key]
    icon = QIcon()
    for mode, tint in ((QIcon.Normal, color), (QIcon.Disabled, disabled_color or color)):
        scale = 4
        pixmap = QPixmap(size * scale, (size + 2 * drop) * scale); pixmap.fill(Qt.transparent); pixmap.setDevicePixelRatio(scale)
        p = QPainter(pixmap); p.setRenderHint(QPainter.Antialiasing)
        p.translate(0, 2 * drop)
        p.scale(size / 24, size / 24)
        stroke, fill = _shape(name)
        if fill is not None:
            p.setPen(Qt.NoPen); p.setBrush(QColor(tint)); p.drawPath(fill)
        else:
            pen = QPen(QColor(tint), 2); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPath(stroke)
        p.end()
        icon.addPixmap(pixmap, mode)
    _CACHE[key] = icon
    return icon
