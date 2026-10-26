"""Draws a device frame (phone, tablet or monitor) around an embedded web view.

Geometry is worked out in CSS pixels in the device's portrait orientation, then
scaled (and rotated for landscape) when painting.
"""
import math

from PySide6.QtCore import QRect, QRectF, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTransform, QLinearGradient
from PySide6.QtWidgets import QWidget

from devices import Device

BODY = QColor("#1c1c1e")
BODY_EDGE = QColor("#48484a")
MONITOR_BODY = QColor("#2a2a2d")
SCREEN_BG = QColor("#ffffff")
INK = QColor("#111111")
STAND_H = 80


class DeviceFrame(QWidget):
    def __init__(self, device: Device, landscape: bool = False, parent=None):
        super().__init__(parent)
        self.device = device
        self.landscape = landscape and device.rotatable
        self.scale = 1.0
        self.content = None

    # ── geometry (CSS px, current orientation) ───────────────────────────────
    def _bezels(self):
        t, r, b, l = self.device.bezel
        # Turning the device a quarter-turn left moves its right edge to the top.
        return (r, b, l, t) if self.landscape else (t, r, b, l)

    def _screen_size(self):
        w, h = self.device.width, self.device.height
        return (h, w) if self.landscape else (w, h)

    def _insets(self):
        return self.device.insets_landscape if self.landscape else self.device.insets_portrait

    def _stand_h(self):
        return STAND_H if self.device.style == "monitor" else 0

    def css_size(self):
        t, r, b, l = self._bezels()
        sw, sh = self._screen_size()
        return l + sw + r, t + sh + b + self._stand_h()

    def viewport_css(self):
        """Size of the page's layout viewport — what CSS media queries see."""
        sw, sh = self._screen_size()
        it, ir, ib, il = self._insets()
        return sw - il - ir, sh - it - ib

    def content_rect(self) -> QRect:
        t, r, b, l = self._bezels()
        it, ir, ib, il = self._insets()
        vw, vh = self.viewport_css()
        s = self.scale
        # Round the size up: rounding down can make the page see 1439px instead of 1440px
        # and miss a min-width breakpoint.
        return QRect(round((l + il) * s), round((t + it) * s), math.ceil(vw * s), math.ceil(vh * s))

    # ── configuration ────────────────────────────────────────────────────────
    def set_content(self, widget):
        self.content = widget
        widget.setParent(self)
        self._relayout()

    def set_scale(self, s: float):
        self.scale = s
        w, h = self.css_size()
        self.setFixedSize(round(w * s), round(h * s))
        self._relayout()
        self.update()

    def set_landscape(self, on: bool):
        self.landscape = on and self.device.rotatable
        self.set_scale(self.scale)

    def _relayout(self):
        if self.content is not None:
            self.content.setGeometry(self.content_rect())

    # ── painting ─────────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        d, s = self.device, self.scale
        p = QPainter(self)
        if d.style == "bare":                      # plain browser viewport, just an outline
            p.fillRect(self.rect(), SCREEN_BG)
            p.setPen(QPen(QColor("#c7c7cc"), 1))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = QTransform.fromScale(s, s)
        pt, pr, pb, pl = d.bezel
        pw, ph = pl + d.width + pr, pt + d.height + pb          # portrait body size
        # portrait (x, y) -> landscape (y, pw - x): top edge ends up on the left
        tf = (QTransform(0, -1, 1, 0, 0, pw) * base) if self.landscape else base
        p.setTransform(tf)

        if d.style == "monitor":
            self._paint_stand(p, pw, ph)

        body = QPainterPath()
        body.addRoundedRect(QRectF(0, 0, pw, ph), d.body_radius, d.body_radius)
        p.fillPath(body, MONITOR_BODY if d.style == "monitor" else BODY)
        p.setPen(QPen(BODY_EDGE, 2))
        p.drawPath(body)

        screen = QPainterPath()
        screen.addRoundedRect(QRectF(pl, pt, d.width, d.height), d.screen_radius, d.screen_radius)
        p.fillPath(screen, SCREEN_BG)
        self._paint_bezel_features(p, pw, ph)

        if not self.landscape and d.status_bar and d.insets_portrait[0] > 0:
            p.setTransform(base)
            self._paint_status_bar(p, QRectF(pl, pt, d.width, d.insets_portrait[0]))
            p.setTransform(tf)

        self._paint_cutout(p, pw)
        p.setTransform(base)
        self._paint_bottom_bar(p)
        p.end()

    def _paint_stand(self, p, pw, ph):
        neck = QRectF(pw / 2 - 70, ph - 4, 140, STAND_H - 18)
        grad = QLinearGradient(neck.topLeft(), neck.topRight())
        grad.setColorAt(0, QColor("#8e8e93"))
        grad.setColorAt(0.5, QColor("#c7c7cc"))
        grad.setColorAt(1, QColor("#8e8e93"))
        p.fillRect(neck, grad)
        foot = QPainterPath()
        foot.addRoundedRect(QRectF(pw / 2 - 170, ph + STAND_H - 22, 340, 18), 9, 9)
        p.fillPath(foot, QColor("#a1a1a6"))

    def _paint_bezel_features(self, p, pw, ph):
        d = self.device
        pt, _, pb, _ = d.bezel
        p.setPen(Qt.PenStyle.NoPen)
        if d.style == "homebutton":
            p.setBrush(QColor("#3a3a3c"))
            p.drawRoundedRect(QRectF(pw / 2 - 30, pt / 2 - 3, 60, 6), 3, 3)        # earpiece
            p.drawEllipse(QPointF(pw / 2 - 52, pt / 2), 5, 5)                        # camera
            p.setBrush(BODY)
            p.setPen(QPen(QColor("#5a5a5e"), 2))
            p.drawEllipse(QPointF(pw / 2, pt + d.height + pb / 2), 27, 27)          # home button
        elif d.style == "tablet":
            p.setBrush(QColor("#3a3a3c"))
            p.drawEllipse(QPointF(pw / 2, pt / 2), 4, 4)
        elif d.style == "monitor":
            p.setBrush(QColor("#4a4a4e"))
            p.drawEllipse(QPointF(pw / 2, pt / 2), 3, 3)

    def _paint_cutout(self, p, pw):
        d = self.device
        pt = d.bezel[0]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#000000"))
        if d.style == "island":
            p.drawRoundedRect(QRectF(pw / 2 - 62.5, pt + 11, 125, 37), 18.5, 18.5)
        elif d.style == "punchhole":
            p.drawEllipse(QPointF(pw / 2, pt + 15), 6, 6)

    def _paint_bottom_bar(self, p):
        d = self.device
        if d.style not in ("island", "punchhole", "tablet"):
            return
        t, r, b, l = self._bezels()
        sw, sh = self._screen_size()
        ib = self._insets()[2]
        if ib < 14:
            return
        bar_w = 134 if d.status_bar == "ios" else 108
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(INK)
        p.drawRoundedRect(QRectF(l + sw / 2 - bar_w / 2, t + sh - ib / 2 - 2.5, bar_w, 5), 2.5, 2.5)

    def _paint_status_bar(self, p, r: QRectF):
        d = self.device
        ios = d.status_bar == "ios"
        p.setPen(INK)
        font = QFont()
        font.setPixelSize(16 if ios else 14)
        font.setWeight(QFont.Weight.DemiBold if ios else QFont.Weight.Medium)
        p.setFont(font)
        cy = r.top() + (29.5 if d.style == "island" else r.height() / 2)
        if ios and d.style == "island":
            ear = r.width() / 2 - 62.5
            p.drawText(QRectF(r.left(), cy - 12, ear, 24), Qt.AlignmentFlag.AlignCenter, "9:41")
            self._paint_status_icons(p, r.left() + r.width() / 2 + 62.5 + ear / 2, cy, centred=True)
        else:
            pad = 16 if not ios else 8
            p.drawText(QRectF(r.left() + pad, cy - 12, 80, 24),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "9:41")
            self._paint_status_icons(p, r.right() - pad, cy, centred=False)

    def _paint_status_icons(self, p, x, cy, centred):
        """Signal bars, Wi-Fi and battery. x is the group's centre (centred) or right edge."""
        group_w = 17 + 6 + 16 + 6 + 27
        left = x - group_w / 2 if centred else x - group_w
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(INK)
        for i, h in enumerate((4, 6, 8.5, 11)):                               # signal
            p.drawRoundedRect(QRectF(left + i * 4.5, cy + 5.5 - h, 3, h), 1, 1)
        wx, wy = left + 23 + 8, cy + 5                                       # wifi
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc_pen = QPen(INK, 2.2)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc_pen)
        for radius in (3, 7, 11):
            p.drawArc(QRectF(wx - radius, wy - radius, radius * 2, radius * 2), 45 * 16, 90 * 16)
        bx = left + 23 + 16 + 6                                              # battery
        p.setPen(QPen(INK, 1.2))
        p.drawRoundedRect(QRectF(bx, cy - 6, 24, 12), 3.5, 3.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(INK)
        p.drawRoundedRect(QRectF(bx + 2, cy - 4, 17, 8), 2, 2)
        p.drawRoundedRect(QRectF(bx + 25, cy - 2.5, 2, 5), 1, 1)
