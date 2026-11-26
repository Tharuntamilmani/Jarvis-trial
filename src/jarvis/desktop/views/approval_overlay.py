"""Native PySide6 fail-closed ApprovalOverlay for sensitive tool calls (Phase 7.2)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.viewmodels.app_state import AppState


class ApprovalOverlay(QFrame):
    """Interactive approval card; closing or timing out always denies."""

    def __init__(
        self,
        state: AppState,
        *,
        on_decision: Callable[[str, bool], Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("GlassPanel")
        self._state = state
        self._on_decision = on_decision
        self._request_id: str | None = None
        self._remaining_seconds = 0

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

        self._build_ui()
        self._state.approval_requested.connect(self._show_request)
        self._state.approval_decided.connect(self._on_external_decision)
        self._state.kill_switch_changed.connect(self._on_kill_switch)
        self.setVisible(False)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("APPROVAL REQUIRED")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.risk_label = QLabel("ELEVATED")
        self.risk_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.risk_label)
        layout.addLayout(header)

        self.action_label = QLabel("")
        self.action_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.action_label)

        self.target_label = QLabel("")
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #7f95b2; font-size: 11px;")
        layout.addWidget(self.detail_label)

        self.countdown_label = QLabel("")
        self.countdown_label.setStyleSheet("color: #ffb020; font-size: 11px;")
        layout.addWidget(self.countdown_label)

        btn_row = QHBoxLayout()
        self.deny_btn = QPushButton("Deny")
        self.deny_btn.clicked.connect(lambda: self._decide(False))
        btn_row.addWidget(self.deny_btn)

        btn_row.addStretch(1)
        self.approve_btn = QPushButton("Approve")
        self.approve_btn.clicked.connect(lambda: self._decide(True))
        btn_row.addWidget(self.approve_btn)
        layout.addLayout(btn_row)

    def _show_request(self, payload: dict[str, Any]) -> None:
        if self._state.kill_switch_active:
            req_id = str(payload.get("request_id") or "")
            if req_id and self._on_decision is not None:
                self._on_decision(req_id, False)
            return

        self._request_id = str(payload.get("request_id") or "")
        tool = str(payload.get("tool_id") or payload.get("action") or "action")
        risk = str(payload.get("risk_level") or "elevated").upper()
        target = str(payload.get("target") or "")
        summary = str(payload.get("summary") or "")
        agent_id = str(payload.get("agent_run_id") or "")

        self.action_label.setText(f"Tool: {tool}")
        self.risk_label.setText(risk)
        color = "#ff4d5e" if risk == "DESTRUCTIVE" else "#ffb020"
        self.risk_label.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 11px;"
        )
        self.target_label.setText(f"Target: {target}" if target else summary)
        extra_parts = [p for p in (summary, f"Agent: {agent_id}" if agent_id else "") if p]
        self.detail_label.setText(" · ".join(extra_parts))

        self._remaining_seconds = max(1, int(float(payload.get("timeout_seconds") or 30.0)))
        self.countdown_label.setText(f"Expires in {self._remaining_seconds}s")
        self._timer.start()
        self.setVisible(True)

    def _on_tick(self) -> None:
        self._remaining_seconds -= 1
        if self._remaining_seconds <= 0:
            self._decide(False)
            return
        self.countdown_label.setText(f"Expires in {self._remaining_seconds}s")

    def _on_external_decision(
        self, request_id: str, approved: bool, decided_by: str
    ) -> None:
        del approved, decided_by
        if self._request_id and request_id == self._request_id:
            self._clear_overlay()

    def _on_kill_switch(self, active: bool) -> None:
        if active and self._request_id is not None:
            self._decide(False)

    def _decide(self, approved: bool) -> None:
        req_id = self._request_id
        self._clear_overlay()
        if req_id and self._on_decision is not None:
            self._on_decision(req_id, approved)

    def _clear_overlay(self) -> None:
        self._timer.stop()
        self._request_id = None
        self.setVisible(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._request_id is not None:
            self._decide(False)
        super().closeEvent(event)
