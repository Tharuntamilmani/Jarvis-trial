"""Animated AI orb used by the HUD and the floating assistant.

Custom-painted orbital core with a volumetric halo, precision concentric
telemetry rings, and a smooth pulse animation (QPropertyAnimation) that
intensifies with the assistant state (idle/listening/thinking/speaking).
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    Property,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

_STATE_COLORS = {
    "idle": ("#38bdf8", "#0369a1"),       # sky cyan
    "listening": ("#34d399", "#059669"),  # emerald
    "thinking": ("#fbbf24", "#b45309"),   # amber
    "speaking": ("#60a5fa", "#1d4ed8"),   # cobalt blue
    "kill_switch": ("#f87171", "#991b1b"),  # crimson
}


class OrbWidget(QWidget):
    """Pulsing orbital core visualization reacting to assistant state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self._state = "idle"
        self._pulse = 1.0  # 1.0 = base size, animated up and down
        self._raw_level = 0.0
        self._smoothed_level = 0.0
        self._smoothing_alpha = 0.25

        self._anim = QPropertyAnimation(self, b"pulse", self)
        self._anim.setDuration(2200)
        self._anim.setStartValue(1.0)
        self._anim.setKeyValueAt(0.5, 1.08)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.start()

    # -- Pulse property (animated) ----------------------------------------------

    def get_pulse(self) -> float:
        return self._pulse

    def set_pulse(self, value: float) -> None:
        self._pulse = value
        self.update()

    pulse = Property(float, get_pulse, set_pulse)

    # -- State -------------------------------------------------------------------

    def set_state(self, state: str) -> None:
        if state not in _STATE_COLORS:
            state = "idle"
        if state != self._state:
            self._state = state
            self.update()

    @property
    def state(self) -> str:
        return self._state

    @property
    def smoothed_level(self) -> float:
        """EMA-smoothed audio level in [0.0, 1.0] (avoids RMS jitter)."""
        return self._smoothed_level

    def set_audio_level(self, level: float) -> None:
        """Update target audio level and apply exponential smoothing."""
        clamped = max(0.0, min(1.0, float(level)))
        self._raw_level = clamped
        self._smoothed_level = (
            self._smoothing_alpha * clamped
            + (1.0 - self._smoothing_alpha) * self._smoothed_level
        )
        if self._smoothed_level < 0.002 and clamped == 0.0:
            self._smoothed_level = 0.0
        self.update()

    # -- Painting -----------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        outer, inner = _STATE_COLORS.get(self._state, _STATE_COLORS["idle"])
        rect = QRectF(self.rect())
        side = min(rect.width(), rect.height())
        audio_boost = 1.0 + self._smoothed_level * 0.22
        radius = side * 0.27 * self._pulse * audio_boost
        center = rect.center()

        # 1. Volumetric outer aura
        glow = QRadialGradient(center, radius * 2.15)
        glow_color = QColor(outer)
        glow_color.setAlpha(min(200, int(55 + self._smoothed_level * 120)))
        glow.setColorAt(0.0, glow_color)
        glow_mid = QColor(inner)
        glow_mid.setAlpha(min(90, int(22 + self._smoothed_level * 45)))
        glow.setColorAt(0.5, glow_mid)
        glow_end = QColor(outer)
        glow_end.setAlpha(0)
        glow.setColorAt(1.0, glow_end)
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, radius * 2.15, radius * 2.15)

        # 2. Outer precision telemetry ring
        ring_r = side * 0.41
        ring_color = QColor(outer)
        ring_color.setAlpha(60)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ring_color, 1.2))
        painter.drawEllipse(center, ring_r, ring_r)

        # 3. Segmented orbital arcs driven smoothly by pulse
        arc_offset = int((self._pulse - 1.0) * 1800)
        arc_rect = QRectF(
            center.x() - ring_r,
            center.y() - ring_r,
            ring_r * 2,
            ring_r * 2,
        )
        arc_color = QColor(outer)
        arc_color.setAlpha(175)
        painter.setPen(QPen(arc_color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(arc_rect, (30 + arc_offset) * 16, 70 * 16)
        painter.drawArc(arc_rect, (210 + arc_offset) * 16, 70 * 16)

        inner_ring_r = side * 0.34
        inner_arc_rect = QRectF(
            center.x() - inner_ring_r,
            center.y() - inner_ring_r,
            inner_ring_r * 2,
            inner_ring_r * 2,
        )
        inner_arc_color = QColor("#e2e8f0")
        inner_arc_color.setAlpha(75)
        painter.setPen(QPen(inner_arc_color, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(inner_arc_rect, (120 - arc_offset) * 16, 55 * 16)
        painter.drawArc(inner_arc_rect, (300 - arc_offset) * 16, 55 * 16)

        # 4. Luminous core sphere
        core = QRadialGradient(
            QPointF(center.x() - radius * 0.2, center.y() - radius * 0.22),
            radius * 1.15,
        )
        core.setColorAt(0.0, QColor("#f8fafc"))
        core.setColorAt(0.32, QColor(outer))
        core.setColorAt(0.85, QColor(inner))
        core.setColorAt(1.0, QColor("#090d14"))
        painter.setBrush(core)
        painter.setPen(QPen(QColor(outer), 1.0))
        painter.drawEllipse(center, radius, radius)
