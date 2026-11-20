"""Reusable status indicator chip widget."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

_STATUS_COLORS = {
    "healthy": "#39d98a",
    "starting": "#ffb020",
    "degraded": "#ffb020",
    "unavailable": "#ff4d5e",
    "stopped": "#7f95b2",
    "unknown": "#7f95b2",
}


class StatusChip(QFrame):
    """Compact indicator: colored dot + service name + status text."""

    def __init__(self, service: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusChip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.service = service

        self._dot = QLabel("●")
        self._dot.setFixedWidth(14)
        self._label = QLabel(service)
        self._detail = QLabel("unknown")
        self._detail.setStyleSheet("color: #7f95b2; font-size: 11px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)
        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        layout.addStretch(1)
        layout.addWidget(self._detail)
        self.set_status("unknown")

    def set_status(self, status: str, detail: str = "") -> None:
        color = _STATUS_COLORS.get(status, _STATUS_COLORS["unknown"])
        self._dot.setStyleSheet(f"color: {color}; font-size: 12px;")
        self._detail.setText(detail or status)

    @property
    def current_status(self) -> str:
        return self._detail.text()
