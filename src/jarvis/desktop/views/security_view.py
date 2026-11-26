"""Native PySide6 Security, Audit & Allowlist View (Phase 7.4)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.security_controller import SecurityController
from jarvis.security.permissions import Capability


class SecurityView(QWidget):
    """Permissions, CommandAllowlist, Audit Log viewer & Kill Switch controls."""

    def __init__(
        self,
        state: AppState,
        controller: SecurityController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._controller = controller
        self._cap_checks: dict[Capability, QCheckBox] = {}
        self._build_ui()
        self._connect_signals()
        self.refresh_all()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("SECURITY, PERMISSIONS & AUDIT")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.kill_switch_label = QLabel("ARMED")
        self.kill_switch_label.setStyleSheet("color: #39d98a; font-weight: bold;")
        header.addWidget(self.kill_switch_label)

        self.kill_btn = QPushButton("Engage Kill Switch")
        self.kill_btn.clicked.connect(self._on_kill)
        header.addWidget(self.kill_btn)

        self.rearm_btn = QPushButton("Rearm")
        self.rearm_btn.clicked.connect(self._on_rearm)
        header.addWidget(self.rearm_btn)
        root.addLayout(header)

        # Capabilities
        cap_panel = QFrame()
        cap_panel.setObjectName("GlassPanel")
        cap_layout = QHBoxLayout(cap_panel)
        cap_layout.setContentsMargins(12, 10, 12, 10)
        for cap in Capability:
            cb = QCheckBox(cap.value)
            cb.toggled.connect(
                lambda checked, c=cap: self._on_cap_toggled(c, checked)
            )
            self._cap_checks[cap] = cb
            cap_layout.addWidget(cb)
        root.addWidget(cap_panel)

        # Tools & Allowlist row
        mid = QHBoxLayout()
        tools_box = QVBoxLayout()
        tools_box.addWidget(QLabel("Registered Tools"))
        self.tools_list = QListWidget()
        tools_box.addWidget(self.tools_list)
        mid.addLayout(tools_box, 1)

        allow_box = QVBoxLayout()
        allow_box.addWidget(QLabel("Command Allowlist"))
        self.allowlist_widget = QListWidget()
        allow_box.addWidget(self.allowlist_widget)
        add_row = QHBoxLayout()
        self.allow_input = QLineEdit()
        self.allow_input.setPlaceholderText("Add exact command…")
        add_row.addWidget(self.allow_input, 1)
        self.allow_add_btn = QPushButton("Add")
        self.allow_add_btn.clicked.connect(self._on_add_allowlist)
        add_row.addWidget(self.allow_add_btn)
        allow_box.addLayout(add_row)
        mid.addLayout(allow_box, 1)
        root.addLayout(mid)

        # Audit log viewer
        audit_hdr = QHBoxLayout()
        audit_hdr.addWidget(QLabel("Audit Trail (Redacted)"))
        self.audit_tool_filter = QLineEdit()
        self.audit_tool_filter.setPlaceholderText("Filter by tool…")
        self.audit_tool_filter.textChanged.connect(lambda _: self._refresh_audit())
        audit_hdr.addWidget(self.audit_tool_filter)
        self.audit_result_filter = QLineEdit()
        self.audit_result_filter.setPlaceholderText("Filter by outcome…")
        self.audit_result_filter.textChanged.connect(lambda _: self._refresh_audit())
        audit_hdr.addWidget(self.audit_result_filter)
        root.addLayout(audit_hdr)

        self.audit_list = QListWidget()
        root.addWidget(self.audit_list, 1)

    def _connect_signals(self) -> None:
        self._state.kill_switch_changed.connect(self._on_kill_switch_changed)
        self._state.audit_record_written.connect(lambda *_: self._refresh_audit())
        if self._controller is not None:
            self._controller.security_changed.connect(self.refresh_all)

    def refresh_all(self) -> None:
        self._refresh_capabilities()
        self._refresh_tools()
        self._refresh_allowlist()
        self._refresh_audit()
        self._on_kill_switch_changed(self._state.kill_switch_active)

    def _refresh_capabilities(self) -> None:
        caps = (
            dict(self._controller.list_capabilities())
            if self._controller is not None
            else {}
        )
        for cap, cb in self._cap_checks.items():
            cb.blockSignals(True)
            cb.setChecked(bool(caps.get(cap, False)))
            cb.blockSignals(False)

    def _refresh_tools(self) -> None:
        self.tools_list.clear()
        if self._controller is None:
            return
        for t in self._controller.list_tools():
            QListWidgetItem(
                f"{t['tool_id']} [{t['risk']}] ({t['caps'] or 'none'})",
                self.tools_list,
            )

    def _refresh_allowlist(self) -> None:
        self.allowlist_widget.clear()
        if self._controller is None:
            return
        for entry in self._controller.list_allowlist():
            QListWidgetItem(entry, self.allowlist_widget)

    def _refresh_audit(self) -> None:
        self.audit_list.clear()
        if self._controller is None:
            return
        entries = self._controller.list_audit_entries(
            tool_filter=self.audit_tool_filter.text(),
            result_filter=self.audit_result_filter.text(),
        )
        for e in entries:
            QListWidgetItem(
                f"#{e['seq']} {e['action']} → {e['result']} ({e['approved_by']})",
                self.audit_list,
            )

    def _on_kill_switch_changed(self, active: bool) -> None:
        if active:
            self.kill_switch_label.setText("KILL SWITCH ENGAGED")
            self.kill_switch_label.setStyleSheet("color: #ff4d5e; font-weight: bold;")
        else:
            self.kill_switch_label.setText("ARMED")
            self.kill_switch_label.setStyleSheet("color: #39d98a; font-weight: bold;")

    def _on_cap_toggled(self, cap: Capability, checked: bool) -> None:
        if self._controller is not None:
            self._controller.set_capability_enabled(cap, checked)

    def _on_add_allowlist(self) -> None:
        if self._controller is None:
            return
        if self._controller.add_allowlist_command(self.allow_input.text()):
            self.allow_input.clear()

    def _on_kill(self) -> None:
        if self._controller is not None:
            self._controller.trigger_kill_switch()

    def _on_rearm(self) -> None:
        if self._controller is not None:
            self._controller.rearm_kill_switch()
