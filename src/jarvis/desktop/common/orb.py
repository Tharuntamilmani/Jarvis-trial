"""Animated AI orb used by the HUD and the floating assistant.

Custom-painted radial glow with a pulse animation (QPropertyAnimation) that
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
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

_STATE_COLORS = {
    "idle": ("#00d4ff", "#0a7ea4"),      # cyan
    "listening": ("#39d98a", "#1a8f5a"), # green
    "thinking": ("#ffb020", "#b07800"),  # amber
    "speaking": ("#7aa7ff", "#2f5fc4"),  # blue
    "kill_switch": ("#ff4d5e", "#8f1f2a"),  # red
}


class OrbWidget(QWidget):
    """Pulsing core visualization reacting to assistant state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self._state = "idle"
        self._pulse = 1.0  # 1.0 = base size, animated up and down

        self._anim = QPropertyAnimation(self, b"pulse")
        self._anim.setDuration(1600)
        self._anim.setStartValue(1.0)
        self._anim.setKeyValueAt(0.5, 1.12)
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

    # -- Painting -----------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        outer, inner = _STATE_COLORS.get(self._state, _STATE_COLORS["idle"])
        rect = QRectF(self.rect())
        side = min(rect.width(), rect.height())
        radius = side * 0.36 * self._pulse
        center = rect.center()

        # Outer glow
        glow = QRadialGradient(center, radius * 1.9)
        glow_color = QColor(outer)
        glow_color.setAlpha(70)
        glow.setColorAt(0.0, glow_color)
        glow_end = QColor(outer)
        glow_end.setAlpha(0)
        glow.setColorAt(1.0, glow_end)
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, radius * 1.9, radius * 1.9)

        # Core
        core = QRadialGradient(QPointF(center.x(), center.y() - radius * 0.15), radius)
        core.setColorAt(0.0, QColor("#ffffff"))
        core.setColorAt(0.25, QColor(outer))
        core.setColorAt(1.0, QColor(inner))
        painter.setBrush(core)
        painter.drawEllipse(center, radius, radius)

        # Ring
        ring = QColor(outer)
        ring.setAlpha(160)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(ring)
        painter.drawEllipse(center, radius * 1.12 * self._pulse, radius * 1.12 * self._pulse)
        painter.end()
