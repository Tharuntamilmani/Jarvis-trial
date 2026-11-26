"""Reusable status indicator chip widget."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from jarvis.desktop.theme import theme

_STATUS_COLORS = {
    "healthy": theme.ACCENT_OK,
    "starting": theme.ACCENT_WARN,
    "degraded": theme.ACCENT_WARN,
    "unavailable": theme.ACCENT_DANGER,
    "stopped": theme.TEXT_MUTED,
    "unknown": theme.TEXT_MUTED,
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
        self._label = QLabel(service.replace("_", " ").title())
        self._label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-weight: 500;")
        self._detail = QLabel("unknown")
        self._detail.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)
        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        layout.addStretch(1)
        layout.addWidget(self._detail)
        self.set_status("unknown")

    def set_status(self, status: str, detail: str = "") -> None:
        color = _STATUS_COLORS.get(status, _STATUS_COLORS["unknown"])
        self._dot.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._detail.setText(detail or status)

    @property
    def current_status(self) -> str:
        return self._detail.text()
