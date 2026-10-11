#!/usr/bin/env python3
"""Keylight: PyQt6 front end for the Gigabyte keyboard backlight.

Talks to the keyboard through kbd.py (same folder), so the CLI and this window
share one saved colour/brightness. The keyboard has a single lamp: one colour
for the whole board. Needs access to the LampArray hidraw node, see
70-gigabyte-kbd.rules; the window explains this itself if access is missing.
"""
import colorsys
import math
import os
import shlex
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kbd  # noqa: E402

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QGuiApplication, QIcon,  # noqa: E402
                         QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QHBoxLayout, QLabel,  # noqa: E402
                             QLineEdit, QToolTip, QVBoxLayout, QWidget)

CYAN = QColor("#00e5ff")
RED = QColor("#ff4d6d")
AMBER = QColor("#ffc21a")
GREEN = QColor("#19ff7a")
DIM = QColor("#5f7090")
TEXT = QColor("#d6e6ff")
INK = QColor("#05070d")
MONO = ["Ubuntu Mono", "DejaVu Sans Mono", "monospace"]
SANS = ["Ubuntu", "Noto Sans", "DejaVu Sans", "sans-serif"]

PRESETS = [("CYAN", "#00f0ff"), ("BLUE", "#2f6bff"), ("VIOLET", "#8a3dff"), ("MAGENTA", "#ff2bd6"),
           ("RED", "#ff2a4a"), ("ORANGE", "#ff7a1a"), ("AMBER", "#ffc21a"), ("LIME", "#7dff2e"),
           ("GREEN", "#19ff7a"), ("WHITE", "#ffffff")]

LINK_TEXT = {"ok": ("LINK ESTABLISHED", GREEN), "noaccess": ("ACCESS DENIED", RED),
             "missing": ("DEVICE NOT FOUND", AMBER), "error": ("LINK LOST // RETRYING", AMBER),
             "unknown": ("SCANNING...", DIM)}

FIX_CMD = "sudo sh " + shlex.quote(os.path.join(HERE, "install-rule.sh"))


def font(families, px, bold=False, spacing=0.0):
    f = QFont()
    f.setFamilies(families)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def alpha(color, a):
    c = QColor(color)
    c.setAlpha(int(max(0, min(255, a))))
    return c


def lit(rgb, pct):
    """The colour the keyboard actually shows: RGB scaled by brightness."""
    return QColor(*(kbd.scale(v, pct) for v in rgb))


def glow(p, center, r_in, r_out, color, strength):
    g = QRadialGradient(center, r_out)
    g.setColorAt(0.0, alpha(color, strength))
    g.setColorAt(min(0.99, r_in / r_out), alpha(color, strength))
    g.setColorAt(1.0, alpha(color, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(center, r_out, r_out)


def parse_hex(text):
    t = text.strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6:
        return None
    try:
        return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def make_icon(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
    p.fillPath(bg, QColor("#080c18"))
    g = QConicalGradient(QPointF(s / 2, s / 2), 0)
    for i in range(13):
        g.setColorAt(i / 12, QColor.fromHsvF(i / 12 % 1.0, 1, 1))
    ring = QPainterPath()
    ring.addEllipse(QPointF(s / 2, s / 2), s * 0.38, s * 0.38)
    inner = QPainterPath()
    inner.addEllipse(QPointF(s / 2, s / 2), s * 0.27, s * 0.27)
    p.fillPath(ring.subtracted(inner), QBrush(g))
    glow(p, QPointF(s / 2, s / 2), s * 0.12, s * 0.27, CYAN, 120)
    p.setBrush(QColor("#d9fbff"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(s / 2, s / 2), s * 0.11, s * 0.11)
    p.end()
    return pm


class Backlight:
    """Owns the hidraw fd. Failures become a status string, never an exception."""

    def __init__(self):
        self.fd = None
        self.count = 1
        self.status = "unknown"
        self._last_write = 0.0

    def _open(self):
        if self.fd is not None:
            return True
        try:
            path = kbd.find_device()
        except SystemExit:
            self.status = "missing"
            return False
        try:
            self.fd = os.open(path, os.O_RDWR)
            self.count = max(1, kbd.attributes(self.fd)["count"])
        except PermissionError:
            self.status = "noaccess"
            return False
        except OSError:
            self._close()
            self.status = "error"
            return False
        self.status = "ok"
        self._last_write = 0.0
        return True

    def _close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None

    def _try(self, action):
        for _ in range(2):  # one retry: the node can vanish and reappear after suspend
            if not self._open():
                return False
            try:
                action()
                self.status = "ok"
                return True
            except OSError:
                self._close()
                self.status = "error"
        return False

    def probe(self):
        return self._open()

    def show(self, rgb, pct):
        def action():
            now = time.monotonic()
            if now - self._last_write > 2.0:  # re-assert host control after idle or resume
                kbd.set_autonomous(self.fd, False)
            kbd.fill(self.fd, self.count, *(kbd.scale(v, pct) for v in rgb), kbd.scale(255, pct))
            self._last_write = now
        return self._try(action)

    def firmware(self):
        return self._try(lambda: kbd.set_autonomous(self.fd, True))


class TitleBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(48)
        self.setMouseTracking(True)
        self.accent = CYAN
        self.hover = None

    def _buttons(self):
        w = self.width()
        return {"min": QRectF(w - 58, 8, 24, 24), "close": QRectF(w - 28, 8, 24, 24)}

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font(SANS, 19, True, 6))
        p.setPen(TEXT)
        p.drawText(QRectF(0, 0, 260, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "KEYLIGHT")
        p.setFont(font(MONO, 10, False, 1.5))
        p.setPen(DIM)
        p.drawText(QRectF(0, 28, self.width(), 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "GIGABYTE A16 // LAMPARRAY 0414:8104")
        for name, r in self._buttons().items():
            hot = self.hover == name
            danger = name == "close"
            edge = RED if (hot and danger) else self.accent
            p.setPen(QPen(alpha(edge, 200 if hot else 90), 1.2))
            p.setBrush(alpha(RED if danger else self.accent, 60) if hot else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QPen(alpha(TEXT, 230 if hot else 150), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            c = r.center()
            if danger:
                p.drawLine(QPointF(c.x() - 4, c.y() - 4), QPointF(c.x() + 4, c.y() + 4))
                p.drawLine(QPointF(c.x() - 4, c.y() + 4), QPointF(c.x() + 4, c.y() - 4))
            else:
                p.drawLine(QPointF(c.x() - 4.5, c.y() + 3), QPointF(c.x() + 4.5, c.y() + 3))

    def _hit(self, pos):
        for name, r in self._buttons().items():
            if r.contains(pos):
                return name
        return None

    def mouseMoveEvent(self, e):
        hit = self._hit(e.position())
        if hit != self.hover:
            self.hover = hit
            self.update()

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit(e.position())
        if hit == "close":
            self.window().close()
        elif hit == "min":
            self.window().showMinimized()
        else:
            handle = self.window().windowHandle()
            if handle:
                handle.startSystemMove()


class StatusStrip(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(22)
        self.state = "unknown"
        self.mode = "custom"
        self.phase = 0.0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text, color = LINK_TEXT[self.state]
        pulse = 0.55 + 0.45 * math.sin(self.phase * 3)
        c = QPointF(6, self.height() / 2)
        glow(p, c, 2, 11, color, 110 * pulse)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, 3.5, 3.5)
        p.setFont(font(MONO, 11, True, 2))
        p.setPen(alpha(color, 235))
        p.drawText(QRectF(20, 0, self.width() - 20, self.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        p.setPen(DIM)
        p.setFont(font(MONO, 10, False, 2))
        p.drawText(QRectF(0, 0, self.width(), self.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "MODE // " + ("FIRMWARE" if self.mode == "firmware" else "CUSTOM"))


class ColorWheel(QWidget):
    hueChanged = pyqtSignal(float)
    SIZE, R_OUT, R_IN, R_ORB = 290, 128, 108, 84

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setMouseTracking(True)
        self.hue, self.rgb, self.pct = 0.5, (255, 255, 255), 100.0
        self.powered, self.firmware = True, False
        self.accent, self.phase, self._drag = CYAN, 0.0, False

    def set_state(self, hue, rgb, pct, powered, firmware):
        self.hue, self.rgb, self.pct, self.powered, self.firmware = hue, rgb, pct, powered, firmware
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = cy = self.SIZE / 2
        ctr = QPointF(cx, cy)
        acc = self.accent

        for i in range(90):  # tick ring
            a = math.radians(i * 4)
            long = i % 5 == 0
            r1, r2 = 134, 134 + (7 if long else 3.5)
            p.setPen(QPen(alpha(acc, 110 if long else 45), 1.2))
            p.drawLine(QPointF(cx + r1 * math.cos(a), cy - r1 * math.sin(a)),
                       QPointF(cx + r2 * math.cos(a), cy - r2 * math.sin(a)))
        p.setPen(QPen(alpha(acc, 190), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for k in range(2):  # two sweeping arcs
            p.drawArc(QRectF(cx - 141, cy - 141, 282, 282), int((-self.phase * 38 + k * 180) * 16), 34 * 16)

        ring = QPainterPath()
        ring.addEllipse(ctr, self.R_OUT, self.R_OUT)
        hole = QPainterPath()
        hole.addEllipse(ctr, self.R_IN, self.R_IN)
        grad = QConicalGradient(ctr, 0)
        for i in range(13):
            grad.setColorAt(i / 12, QColor.fromHsvF(i / 12 % 1.0, 1, 1))
        p.setOpacity(0.95 if self.powered and not self.firmware else 0.55)
        p.fillPath(ring.subtracted(hole), QBrush(grad))
        p.setOpacity(1)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(alpha(TEXT, 70), 1))
        p.drawEllipse(ctr, self.R_OUT, self.R_OUT)
        p.drawEllipse(ctr, self.R_IN, self.R_IN)

        dash = QPen(alpha(acc, 90), 1)
        dash.setStyle(Qt.PenStyle.DashLine)
        dash.setDashOffset(self.phase * 14)
        p.setPen(dash)
        p.drawEllipse(ctr, self.R_IN - 9, self.R_IN - 9)

        live = self.powered and not self.firmware
        base = lit(self.rgb, self.pct) if live else QColor(16, 21, 38)
        if live:
            glow(p, ctr, self.R_ORB * 0.9, self.R_ORB + 30, base, 40 + 150 * self.pct / 100)
        og = QRadialGradient(ctr, self.R_ORB, QPointF(cx - 22, cy - 30))
        og.setColorAt(0.0, QColor(min(255, base.red() + 90), min(255, base.green() + 90), min(255, base.blue() + 90)))
        og.setColorAt(0.5, base)
        og.setColorAt(1.0, QColor(base.red() // 2, base.green() // 2, base.blue() // 2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(og))
        p.drawEllipse(ctr, self.R_ORB, self.R_ORB)
        if self.firmware:  # slow rainbow sweep: the firmware is in charge
            sweep = QConicalGradient(ctr, -self.phase * 50)
            for i in range(13):
                sweep.setColorAt(i / 12, alpha(QColor.fromHsvF(i / 12 % 1.0, 0.9, 1), 95))
            p.setBrush(QBrush(sweep))
            p.drawEllipse(ctr, self.R_ORB, self.R_ORB)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(alpha(acc, 170), 1.6))
        p.drawEllipse(ctr, self.R_ORB, self.R_ORB)

        a = math.radians(self.hue * 360)
        mid = (self.R_OUT + self.R_IN) / 2
        hp = QPointF(cx + mid * math.cos(a), cy - mid * math.sin(a))
        pure = QColor.fromHsvF(self.hue % 1.0, 1, 1)
        glow(p, hp, 6, 17, pure, 150)
        p.setBrush(pure)
        p.setPen(QPen(QColor("white"), 2.6))
        p.drawEllipse(hp, 8.5, 8.5)

        lum = 0.2126 * base.red() + 0.7152 * base.green() + 0.0722 * base.blue()
        ink = INK if (live and lum > 150) else QColor(235, 245, 255)
        if self.firmware:
            big, small = "AUTO", "FIRMWARE EFFECT"
        elif not self.powered:
            big, small = "OFF", "STANDBY"
        else:
            big, small = "#%02X%02X%02X" % self.rgb, "LUMINANCE %d%%" % round(self.pct)
        p.setPen(alpha(ink, 245))
        p.setFont(font(MONO, 25, True, 2))
        p.drawText(QRectF(cx - 80, cy - 26, 160, 34), Qt.AlignmentFlag.AlignCenter, big)
        p.setPen(alpha(ink, 175))
        p.setFont(font(MONO, 10, False, 2))
        p.drawText(QRectF(cx - 80, cy + 9, 160, 18), Qt.AlignmentFlag.AlignCenter, small)

    def _set_from(self, pos):
        ang = math.atan2(self.SIZE / 2 - pos.y(), pos.x() - self.SIZE / 2)
        self.hueChanged.emit((math.degrees(ang) % 360) / 360)

    def _on_ring(self, pos):
        d = math.hypot(pos.x() - self.SIZE / 2, pos.y() - self.SIZE / 2)
        return self.R_IN - 8 <= d <= self.R_OUT + 12

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._on_ring(e.position()):
            self._drag = True
            self._set_from(e.position())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._set_from(e.position())
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._on_ring(e.position())
                           else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _):
        self._drag = False


class NeonSlider(QWidget):
    valueChanged = pyqtSignal(float)

    def __init__(self, label):
        super().__init__()
        self.label, self.value, self.text = label, 1.0, ""
        self.c0, self.c1, self.accent = QColor("#222a44"), CYAN, CYAN
        self.setFixedHeight(50)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_value(self, v, text):
        self.value, self.text = max(0.0, min(1.0, v)), text
        self.update()

    def _track(self):
        return QRectF(10, 34, self.width() - 20, 6)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font(SANS, 11, True, 3))
        p.setPen(DIM)
        p.drawText(QRectF(0, 0, self.width() / 2, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.label)
        p.setFont(font(MONO, 13, True, 1))
        p.setPen(TEXT)
        p.drawText(QRectF(0, 0, self.width(), 22), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self.text)
        t = self._track()
        for i in range(11):
            x = t.left() + t.width() * i / 10
            p.setPen(QPen(alpha(TEXT, 55), 1))
            p.drawLine(QPointF(x, t.bottom() + 5), QPointF(x, t.bottom() + (9 if i % 5 == 0 else 7)))
        full = QLinearGradient(t.left(), 0, t.right(), 0)
        full.setColorAt(0, self.c0)
        full.setColorAt(1, self.c1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(alpha(TEXT, 22))
        p.drawRoundedRect(t, 3, 3)
        hx = t.left() + t.width() * self.value
        fill = QRectF(t.left(), t.top(), max(0.0, hx - t.left()), t.height())
        p.setBrush(alpha(self.c1, 40))
        p.drawRoundedRect(fill.adjusted(-2, -3, 2, 3), 5, 5)
        p.setBrush(QBrush(full))
        p.drawRoundedRect(fill, 3, 3)
        hc = QPointF(hx, t.center().y())
        tip = self.c1 if self.value > 0.02 else DIM
        glow(p, hc, 5, 17, tip, 120)
        p.setBrush(INK)
        p.setPen(QPen(tip if tip != DIM else TEXT, 2.2))
        p.drawEllipse(hc, 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(alpha(TEXT, 230))
        p.drawEllipse(hc, 2.2, 2.2)

    def _set(self, x):
        t = self._track()
        v = max(0.0, min(1.0, (x - t.left()) / t.width()))
        self.value = v
        self.valueChanged.emit(v)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._set(e.position().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._set(e.position().x())

    def wheelEvent(self, e):
        step = 0.02 if e.angleDelta().y() > 0 else -0.02
        self.value = max(0.0, min(1.0, self.value + step))
        self.valueChanged.emit(self.value)

    def keyPressEvent(self, e):
        step = {Qt.Key.Key_Left: -0.01, Qt.Key.Key_Right: 0.01}.get(Qt.Key(e.key()))
        if step is None:
            return super().keyPressEvent(e)
        self.value = max(0.0, min(1.0, self.value + step))
        self.valueChanged.emit(self.value)


class PresetRow(QWidget):
    picked = pyqtSignal(tuple)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(40)
        self.setMouseTracking(True)
        self.hover, self.current, self.accent = None, None, CYAN
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _centers(self):
        n, pad = len(PRESETS), 16
        step = (self.width() - 2 * pad) / (n - 1)
        return [QPointF(pad + i * step, self.height() / 2) for i in range(n)]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, ((_, hexcode), c) in enumerate(zip(PRESETS, self._centers())):
            col = QColor(hexcode)
            r = 13 if i == self.hover else 10.5
            glow(p, c, r * 0.6, r + 11, col, 105 if i == self.hover else 60)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(c, r, r)
            if self.current == i:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("white"), 1.8))
                p.drawEllipse(c, r + 5, r + 5)

    def _index(self, pos):
        for i, c in enumerate(self._centers()):
            if math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= 16:
                return i
        return None

    def mouseMoveEvent(self, e):
        i = self._index(e.position())
        if i != self.hover:
            self.hover = i
            self.update()
            if i is not None:
                QToolTip.showText(e.globalPosition().toPoint(), PRESETS[i][0], self)
            else:
                QToolTip.hideText()

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        i = self._index(e.position())
        if e.button() == Qt.MouseButton.LeftButton and i is not None:
            c = QColor(PRESETS[i][1])
            self.picked.emit((c.red(), c.green(), c.blue()))


class ChannelBars(QWidget):
    COLORS = [("R", QColor("#ff3b5c")), ("G", QColor("#2bff88")), ("B", QColor("#3b8bff"))]

    def __init__(self):
        super().__init__()
        self.setFixedHeight(64)
        self.rgb = (255, 255, 255)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, ((name, col), v) in enumerate(zip(self.COLORS, self.rgb)):
            y = 4 + i * 20
            p.setFont(font(MONO, 12, True))
            p.setPen(col)
            p.drawText(QRectF(0, y - 3, 16, 16), Qt.AlignmentFlag.AlignCenter, name)
            bar = QRectF(24, y + 2, self.width() - 24 - 40, 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(alpha(TEXT, 22))
            p.drawRoundedRect(bar, 3, 3)
            g = QLinearGradient(bar.left(), 0, bar.right(), 0)
            g.setColorAt(0, alpha(col, 40))
            g.setColorAt(1, col)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * v / 255, bar.height()), 3, 3)
            p.setPen(TEXT)
            p.setFont(font(MONO, 12, True))
            p.drawText(QRectF(self.width() - 38, y - 3, 38, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "%3d" % v)


class KeyboardPreview(QWidget):
    ROWS = [[1] * 14, [1] * 13 + [1.6], [1.5] + [1] * 12 + [1.5], [1.9] + [1] * 11 + [2.1],
            [1.3, 1.3, 1.3, 6.2, 1.3, 1.3, 1.3]]

    def __init__(self):
        super().__init__()
        self.setFixedHeight(92)
        self.rgb, self.pct, self.powered, self.firmware, self.phase = (255, 255, 255), 100.0, True, False, 0.0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, gap, kh = self.width(), 3.5, 12.0
        live = self.powered and not self.firmware
        base = lit(self.rgb, self.pct) if live else QColor(70, 90, 130)
        level = self.pct / 100 if live else 0.3
        if live:
            glow(p, QPointF(w / 2, self.height() / 2), w * 0.25, w * 0.62, base, 55 * level)
        y = 6.0
        for row in self.ROWS:
            total = sum(row)
            unit = (w - gap * (len(row) - 1)) / total
            x = 0.0
            for k in row:
                kw = unit * k
                if self.firmware:
                    col = QColor.fromHsvF((x / w + self.phase * 0.12) % 1.0, 0.85, 1)
                    edge, fill = alpha(col, 190), alpha(col, 55)
                else:
                    edge, fill = alpha(base, 150 + 90 * level), alpha(base, 40 + 60 * level)
                p.setPen(QPen(edge, 1.2))
                p.setBrush(fill)
                p.drawRoundedRect(QRectF(x, y, kw, kh), 3, 3)
                x += kw + gap
            y += kh + gap


class NeonButton(QAbstractButton):
    def __init__(self, text, kind="plain"):
        super().__init__()
        self.setText(text)
        self.kind, self.active, self.accent, self.hover = kind, False, CYAN, False
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, _):
        self.hover = True
        self.update()

    def leaveEvent(self, _):
        self.hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        on = self.active
        if on:
            for i in range(1, 5):
                p.setPen(QPen(alpha(self.accent, 40 - i * 8), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-i, -i, i, i), 10 + i, 10 + i)
        p.setPen(QPen(alpha(self.accent, 230 if (on or self.hover) else 120), 1.5))
        p.setBrush(alpha(self.accent, 80 if on else (36 if self.hover else 10)))
        p.drawRoundedRect(r, 10, 10)
        label = font(SANS, 12, True, 3)
        p.setFont(label)
        tw = p.fontMetrics().horizontalAdvance(self.text())
        icon_w = 22 if self.kind == "power" else 0
        x0 = (self.width() - tw - icon_w) / 2
        ink = QColor("white") if on else TEXT
        p.setPen(ink)
        p.drawText(QRectF(x0 + icon_w, 0, tw + 6, self.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.kind == "power":
            c = QPointF(x0 + 8, self.height() / 2)
            p.setPen(QPen(ink, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(c.x() - 7, c.y() - 7, 14, 14), 120 * 16, 300 * 16)
            p.drawLine(QPointF(c.x(), c.y() - 9), QPointF(c.x(), c.y() - 1))


class AccessBanner(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        head = QLabel("ONE-TIME SETUP REQUIRED")
        head.setStyleSheet("color:#ff4d6d; font-family:'Ubuntu'; font-size:12px; font-weight:bold; letter-spacing:2px;")
        body = QLabel("Run this once in a terminal. The app links up on its own afterwards.")
        body.setWordWrap(True)
        body.setStyleSheet("color:#9fb2d4; font-size:12px;")
        cmd = QLabel(FIX_CMD)
        cmd.setWordWrap(True)
        cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cmd.setStyleSheet("color:#d6e6ff; font-family:'Ubuntu Mono','DejaVu Sans Mono'; font-size:11px;"
                          "background:rgba(255,255,255,12); border-radius:6px; padding:8px;")
        self.copy = NeonButton("COPY COMMAND")
        self.copy.accent = RED
        self.copy.setFixedHeight(38)
        for w in (head, body, cmd, self.copy):
            lay.addWidget(w)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(alpha(RED, 140), 1.2))
        p.setBrush(alpha(RED, 18))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)


class Caption(QLabel):
    def __init__(self, text):
        super().__init__(text)
        self.setStyleSheet("color:#5f7090; font-family:'Ubuntu'; font-size:11px; font-weight:bold; letter-spacing:3px;")


class Window(QWidget):
    MARGIN, WIDTH = 16, 384

    def __init__(self, backlight):
        super().__init__()
        self.bl = backlight
        self.setWindowTitle("Keyboard Lights")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(self.WIDTH)

        has_state = os.path.exists(kbd.STATE)
        rgb, pct = kbd.load_state()
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        self.hue, self.sat, self.pct = h, s, max(0.0, min(100.0, pct * v if v > 0 else pct))
        self.powered, self.mode, self.phase = True, ("custom" if has_state else "firmware"), 0.0
        self.dirty = False

        self.title = TitleBar()
        self.status = StatusStrip()
        self.wheel = ColorWheel()
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.bars = ChannelBars()
        self.sat_slider = NeonSlider("SATURATION")
        self.bri_slider = NeonSlider("LUMINANCE")
        self.presets = PresetRow()
        self.preview = KeyboardPreview()
        self.power = NeonButton("POWER", "power")
        self.firmware_btn = NeonButton("FIRMWARE MODE")
        self.banner = AccessBanner()
        self.banner.hide()

        hex_col = QVBoxLayout()
        hex_col.setSpacing(5)
        hex_col.addWidget(Caption("COLOR HEX"))
        hex_col.addWidget(self.hex)
        hex_col.addStretch()
        hex_wrap = QWidget()
        hex_wrap.setFixedWidth(150)
        hex_wrap.setLayout(hex_col)
        hex_col.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addWidget(hex_wrap)
        row.addWidget(self.bars, 1)
        btns = QHBoxLayout()
        btns.setSpacing(12)
        btns.addWidget(self.power, 1)
        btns.addWidget(self.firmware_btn, 2)

        lay = QVBoxLayout(self)
        m = self.MARGIN
        lay.setContentsMargins(m + 24, m + 14, m + 24, m + 22)
        lay.setSpacing(8)
        lay.addWidget(self.title)
        lay.addWidget(self.status)
        lay.addWidget(self.wheel, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addLayout(row)
        lay.addWidget(self.sat_slider)
        lay.addWidget(self.bri_slider)
        lay.addWidget(self.presets)
        lay.addWidget(self.preview)
        lay.addLayout(btns)
        lay.addWidget(self.banner)

        self.wheel.hueChanged.connect(self._on_hue)
        self.sat_slider.valueChanged.connect(self._on_sat)
        self.bri_slider.valueChanged.connect(self._on_bri)
        self.presets.picked.connect(self._on_rgb)
        self.hex.editingFinished.connect(self._on_hex)
        self.power.clicked.connect(self._toggle_power)
        self.firmware_btn.clicked.connect(self._firmware)
        self.banner.copy.clicked.connect(self._copy_fix)

        self.apply_timer = QTimer(self, singleShot=True, interval=25)
        self.apply_timer.timeout.connect(self._apply)
        self.save_timer = QTimer(self, singleShot=True, interval=600)
        self.save_timer.timeout.connect(self._save)
        self.anim = QTimer(self, interval=33)
        self.anim.timeout.connect(self._tick)
        self.anim.start()
        self.poll = QTimer(self, interval=1500)
        self.poll.timeout.connect(self._poll)
        self.poll.start()

        self._refresh()
        self.bl.probe()
        if has_state:
            self._apply()
        self.banner.setVisible(self.bl.status == "noaccess")
        self._fit()
        self._sync_link()

    def _fit(self):
        lay = self.layout()
        h = lay.totalHeightForWidth(self.WIDTH)  # -1 when no item has height-for-width
        self.setFixedHeight(h if h > 0 else lay.sizeHint().height())

    def rgb(self):
        return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(self.hue % 1.0, self.sat, 1.0))

    # --- user actions -------------------------------------------------------------------------
    def _touched(self):
        self.mode, self.powered, self.dirty = "custom", True, True
        self._refresh()
        if not self.apply_timer.isActive():  # throttle, not debounce: follow the drag live
            self.apply_timer.start()
        self.save_timer.start()

    def _on_hue(self, h):
        self.hue = h
        if self.sat < 0.05:  # a hue change on a white colour would otherwise show nothing
            self.sat = 1.0
        self._touched()

    def _on_sat(self, v):
        self.sat = v
        self._touched()

    def _on_bri(self, v):
        self.pct = v * 100
        self._touched()

    def _on_rgb(self, rgb):
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        if v <= 0:
            return
        self.hue, self.sat = h, s
        self._touched()

    def _on_hex(self):
        rgb = parse_hex(self.hex.text())
        if rgb is None:
            self._refresh()
        else:
            self._on_rgb(rgb)
            self.hex.clearFocus()

    def _toggle_power(self):
        self.powered = not self.powered
        self.mode = "custom"
        self._refresh()
        self._apply()

    def _firmware(self):
        self.mode, self.powered = "firmware", True
        self._refresh()
        self._apply()

    def _copy_fix(self):
        QGuiApplication.clipboard().setText(FIX_CMD)
        self.banner.copy.setText("COPIED")
        QTimer.singleShot(1500, lambda: self.banner.copy.setText("COPY COMMAND"))

    # --- hardware -----------------------------------------------------------------------------
    def _apply(self):
        if self.mode == "firmware":
            self.bl.firmware()
        elif not self.powered:
            self.bl.show((0, 0, 0), 0)
        else:
            self.bl.show(self.rgb(), self.pct)
        self._sync_link()

    def _save(self):
        if not self.dirty:
            return
        try:
            kbd.save_state(self.rgb(), self.pct)
        except OSError:
            pass

    def _poll(self):
        if self.bl.status != "ok":
            if self.bl.probe():
                self._apply()
            self._sync_link()

    def _sync_link(self):
        self.status.state = self.bl.status if self.bl.status in LINK_TEXT else "unknown"
        was = self.banner.isVisible()
        self.banner.setVisible(self.bl.status == "noaccess")
        if was != self.banner.isVisible():
            self._fit()
        self.status.update()

    # --- presentation -------------------------------------------------------------------------
    def _refresh(self):
        rgb = self.rgb()
        live = self.mode == "custom" and self.powered
        accent = QColor(*rgb) if live else CYAN
        pure = QColor(*rgb)
        self.wheel.set_state(self.hue, rgb, self.pct, self.powered, self.mode == "firmware")
        self.sat_slider.c0, self.sat_slider.c1 = QColor("white"), pure
        self.sat_slider.set_value(self.sat, "%d%%" % round(self.sat * 100))
        self.bri_slider.c0, self.bri_slider.c1 = QColor("#1b2238"), pure
        self.bri_slider.set_value(self.pct / 100, "%d%%" % round(self.pct))
        self.bars.rgb = rgb
        self.bars.update()
        if not self.hex.hasFocus():
            self.hex.setText("#%02X%02X%02X" % rgb)
        self.hex.setStyleSheet(
            "QLineEdit{background:rgba(255,255,255,10);border:1px solid rgba(255,255,255,45);border-radius:8px;"
            "color:#d6e6ff;padding:7px 10px;font-family:'Ubuntu Mono','DejaVu Sans Mono';font-size:20px;"
            "font-weight:bold;letter-spacing:2px;}QLineEdit:focus{border:1px solid %s;}" % accent.name())
        match = None
        for i, (_, code) in enumerate(PRESETS):
            c = QColor(code)
            if abs(c.red() - rgb[0]) + abs(c.green() - rgb[1]) + abs(c.blue() - rgb[2]) < 18:
                match = i
        self.presets.current = match if live else None
        self.presets.update()
        self.preview.rgb, self.preview.pct = rgb, self.pct
        self.preview.powered, self.preview.firmware = self.powered, self.mode == "firmware"
        self.preview.update()
        self.power.active = self.powered and self.mode == "custom"
        self.firmware_btn.active = self.mode == "firmware"
        self.status.mode = self.mode
        for w in (self.title, self.wheel, self.sat_slider, self.bri_slider, self.presets, self.power,
                  self.firmware_btn):
            w.accent = accent
            w.update()
        self.accent = accent
        self.update()

    def _tick(self):
        self.phase += 0.033
        self.wheel.phase = self.preview.phase = self.status.phase = self.phase
        self.wheel.update()
        self.status.update()
        if self.mode == "firmware":
            self.preview.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.MARGIN
        panel = QRectF(self.rect()).adjusted(m, m, -m, -m)
        acc = getattr(self, "accent", CYAN)
        for i in range(m - 2, 0, -1):  # outer glow
            p.setPen(QPen(alpha(acc, 38 * (1 - i / m) ** 2), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(panel.adjusted(-i, -i, i, i), 20 + i, 20 + i)
        path = QPainterPath()
        path.addRoundedRect(panel, 20, 20)
        bg = QLinearGradient(0, panel.top(), 0, panel.bottom())
        bg.setColorAt(0, QColor("#0b1122"))
        bg.setColorAt(1, QColor("#060912"))
        p.fillPath(path, QBrush(bg))
        p.save()
        p.setClipPath(path)
        p.setPen(QPen(alpha(TEXT, 9), 1))
        x = panel.left()
        while x < panel.right():
            p.drawLine(QPointF(x, panel.top()), QPointF(x, panel.bottom()))
            x += 26
        y = panel.top()
        while y < panel.bottom():
            p.drawLine(QPointF(panel.left(), y), QPointF(panel.right(), y))
            y += 26
        wash = QRadialGradient(QPointF(panel.center().x(), panel.top() + 250), 300)
        wash.setColorAt(0, alpha(acc, 34))
        wash.setColorAt(1, alpha(acc, 0))
        p.fillRect(panel, QBrush(wash))
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(alpha(acc, 100), 1.2))
        p.drawRoundedRect(panel, 20, 20)
        p.setPen(QPen(alpha(acc, 230), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        L, o = 18, 9
        for sx, sy, ax, ay in ((panel.left() + o, panel.top() + o, 1, 1), (panel.right() - o, panel.top() + o, -1, 1),
                               (panel.left() + o, panel.bottom() - o, 1, -1),
                               (panel.right() - o, panel.bottom() - o, -1, -1)):
            p.drawLine(QPointF(sx, sy), QPointF(sx + ax * L, sy))
            p.drawLine(QPointF(sx, sy), QPointF(sx, sy + ay * L))

    def closeEvent(self, e):
        self._save()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("keyboard-lights")
    app.setDesktopFileName("keyboard-lights")
    app.setWindowIcon(QIcon(make_icon(128)))
    window = Window(Backlight())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
