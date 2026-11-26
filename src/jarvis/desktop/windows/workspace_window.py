"""Premium workspace window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.common.status_chip import StatusChip
from jarvis.config.preferences import PreferencesService
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.desktop.viewmodels.security_controller import SecurityController
from jarvis.desktop.viewmodels.vision_controller import VisionController
from jarvis.desktop.viewmodels.voice_controller import VoiceController
from jarvis.desktop.views.agents_view import AgentsView
from jarvis.desktop.views.chat_view import ChatView
from jarvis.desktop.views.history_view import HistoryView
from jarvis.desktop.views.memory_view import MemoryView
from jarvis.desktop.views.security_view import SecurityView
from jarvis.desktop.views.settings_view import SettingsView

_NAV_ITEMS = ("Chat", "History", "Memory", "Agents", "Settings", "Status", "Security")
_PROVIDER_NAMES = {"ollama": "Ollama", "groq": "Groq"}


class WorkspaceWindow(QWidget):
    """Premium desktop workspace. Closing this window never affects the
    engine or the other windows; the shared ChatController outlives it."""

    show_hud_requested = Signal()
    show_floating_requested = Signal()
    closed = Signal()

    def __init__(
        self,
        state: AppState,
        chat_controller: ChatController | None = None,
        parent: QWidget | None = None,
        *,
        voice_controller: VoiceController | None = None,
        vision_controller: VisionController | None = None,
        security_controller: SecurityController | None = None,
        preferences_service: PreferencesService | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window,
        )
        self.setWindowTitle("JARVIS — Workspace")
        self.resize(1120, 720)
        self.setMinimumSize(820, 560)
        self.setObjectName("WorkspaceRoot")
        self._state = state
        self._controller = chat_controller
        self._voice = voice_controller
        self._vision = vision_controller
        self._security = security_controller
        self._preferences = preferences_service
        self._chips: dict[str, StatusChip] = {}
        self._provider_dots: dict[str, QLabel] = {}
        self._provider_details: dict[str, QLabel] = {}
        self._closed_emitted = False

        self._build_ui()
        self._connect_state()
        self._refresh_provider_rows(
            self._controller.provider_statuses if self._controller else {}
        )

    # -- UI -------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._make_sidebar())

        content = QStackedWidget()
        content.addWidget(ChatView(self._state, self._controller))
        self.history_view = HistoryView(self._controller)
        self.history_view.conversation_opened.connect(
            lambda _cid: self.nav.setCurrentRow(0)
        )
        content.addWidget(self.history_view)
        self.memory_view = MemoryView()
        content.addWidget(self.memory_view)
        self.agents_view = AgentsView(self._state)
        content.addWidget(self.agents_view)
        self.settings_view = self._make_settings_page()
        content.addWidget(self.settings_view)
        content.addWidget(self._make_status_page())
        self.security_view = SecurityView(self._state, self._security)
        content.addWidget(self.security_view)
        self.content = content
        root.addWidget(content, 1)

        self.nav.currentRowChanged.connect(self._on_nav_changed)

    def _make_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("WorkspaceSidebar")
        sidebar.setFixedWidth(238)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(6)

        brand = QLabel("JARVIS")
        brand.setObjectName("SidebarBrand")
        layout.addWidget(brand)
        caption = QLabel("WORKSPACE")
        caption.setObjectName("SidebarCaption")
        layout.addWidget(caption)
        layout.addSpacing(4)

        self.new_chat_button = QPushButton("＋  New Chat")
        self.new_chat_button.setObjectName("NewChatButton")
        self.new_chat_button.setEnabled(
            self._controller is not None
            and self._controller.persistence_available
            and not self._controller.finalizing
        )
        self.new_chat_button.clicked.connect(self._new_chat)
        layout.addWidget(self.new_chat_button)
        layout.addSpacing(4)

        conversations = QLabel("CONVERSATIONS")
        conversations.setObjectName("SidebarCaption")
        layout.addWidget(conversations)
        self.conversations = QListWidget()
        self.conversations.setObjectName("ConversationList")
        self.conversations.setMaximumHeight(82)
        self.conversations.itemClicked.connect(self._on_sidebar_conversation_clicked)
        layout.addWidget(self.conversations)

        layout.addSpacing(2)
        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        for item in _NAV_ITEMS:
            QListWidgetItem(item, self.nav)
        self.nav.setCurrentRow(0)
        layout.addWidget(self.nav, 1)

        layout.addWidget(self._make_provider_panel())
        layout.addSpacing(4)

        self.hud_button = QPushButton("HUD Mode")
        self.hud_button.setObjectName("GhostButton")
        self.floating_button = QPushButton("Floating Mode")
        self.floating_button.setObjectName("GhostButton")
        self.hud_button.clicked.connect(self.show_hud_requested.emit)
        self.floating_button.clicked.connect(self.show_floating_requested.emit)
        layout.addWidget(self.hud_button)
        layout.addWidget(self.floating_button)
        return sidebar

    def _make_provider_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("ProviderPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel("PROVIDERS")
        title.setObjectName("SidebarCaption")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setObjectName("IconButton")
        self.refresh_button.setToolTip("Refresh provider status")
        self.refresh_button.setEnabled(self._controller is not None)
        self.refresh_button.clicked.connect(self._refresh_providers)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        for provider_id, name in _PROVIDER_NAMES.items():
            row = QHBoxLayout()
            row.setSpacing(7)
            dot = QLabel("●")
            dot.setFixedWidth(10)
            dot.setStyleSheet("color: #7f95b2; font-size: 10px;")
            label = QLabel(name)
            label.setObjectName("ProviderName")
            detail = QLabel("not checked")
            detail.setObjectName("ProviderDetail")
            detail.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(dot)
            row.addWidget(label)
            row.addStretch(1)
            row.addWidget(detail)
            layout.addLayout(row)
            self._provider_dots[provider_id] = dot
            self._provider_details[provider_id] = detail
        return panel

    def _make_placeholder(self, name: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        title = QLabel(name)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        info = QLabel(f"{name} view arrives in a later phase.")
        info.setObjectName("PlaceholderText")
        layout.addWidget(info)
        layout.addStretch(1)
        return page

    def _make_status_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        title = QLabel("System Status")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        panel = QFrame()
        panel.setObjectName("GlassPanel")
        panel_layout = QVBoxLayout(panel)
        for service in ("engine", "database", "providers", "kill_switch"):
            chip = StatusChip(service)
            self._chips[service] = chip
            panel_layout.addWidget(chip)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    # -- State ---------------------------------------------------------------

    def _connect_state(self) -> None:
        self._state.service_status_changed.connect(self._on_service_status)
        self._state.provider_health_changed.connect(self._on_provider_health)
        if self._controller is not None:
            self._controller.provider_status_changed.connect(
                self._refresh_provider_rows
            )
            self._controller.status_changed.connect(self._update_new_chat_state)
            self._controller.conversations_changed.connect(
                self._refresh_sidebar_conversations
            )
            self._controller.conversation_reset.connect(
                self._refresh_sidebar_conversations
            )
        self._refresh_sidebar_conversations()
        for service, chip in self._chips.items():
            status = self._state.service_statuses.get(service)
            if status is not None:
                chip.set_status(
                    status, self._state.service_details.get(service, "")
                )

    def _update_new_chat_state(self) -> None:
        self.new_chat_button.setEnabled(
            self._controller is not None
            and self._controller.persistence_available
            and not self._controller.finalizing
        )

    def _on_service_status(self, service: str, status: str, detail: str) -> None:
        key = service if service in self._chips else None
        if key is None:
            if service in ("ollama", "groq"):
                key = "providers"
            else:
                return
        self._chips[key].set_status(status, detail)

    def _on_provider_health(
        self, provider: str, available: bool, detail: str
    ) -> None:
        dot = self._provider_dots.get(provider)
        label = self._provider_details.get(provider)
        if dot is None or label is None:
            return
        color = "#39d98a" if available else "#ff4d5e"
        dot.setStyleSheet(f"color: {color}; font-size: 10px;")
        label.setText("online" if available else "offline")
        label.setToolTip(detail)

    def _refresh_provider_rows(self, statuses) -> None:
        for provider_id, dot in self._provider_dots.items():
            label = self._provider_details[provider_id]
            status = statuses.get(provider_id) if statuses else None
            if status is None:
                color = "#7f95b2"
                text = "not checked"
                tooltip = ""
            elif not status.configured:
                color = "#7f95b2"
                text = "not configured"
                tooltip = status.detail
            elif status.available:
                color = "#39d98a"
                text = status.model or "ready"
                tooltip = status.detail
            else:
                color = "#ff4d5e"
                text = "offline"
                tooltip = status.detail
            dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            label.setText(text)
            label.setToolTip(tooltip)

    def _on_nav_changed(self, row: int) -> None:
        # Pages are built in exactly the same order as the nav items, so
        # Status (row 5) maps to the Status page (index 5).
        if 0 <= row < self.content.count():
            self.content.setCurrentIndex(row)

    # -- Actions ---------------------------------------------------------------

    def _new_chat(self) -> None:
        if self._controller is None:
            return
        self._controller.new_chat()
        self.nav.setCurrentRow(0)

    def _refresh_sidebar_conversations(self) -> None:
        self.conversations.blockSignals(True)
        try:
            self.conversations.clear()
            items = (
                self._controller.conversations_list
                if self._controller is not None
                else ()
            )
            if not items:
                QListWidgetItem("Current session", self.conversations)
                self.conversations.setCurrentRow(0)
                return
            active_id = (
                self._controller.conversation_id
                if self._controller is not None
                else None
            )
            selected_row = -1
            for idx, summary in enumerate(items[:10]):
                item = QListWidgetItem(summary.title, self.conversations)
                item.setData(Qt.ItemDataRole.UserRole, summary.id)
                if summary.id == active_id:
                    selected_row = idx
            if selected_row >= 0:
                self.conversations.setCurrentRow(selected_row)
        finally:
            self.conversations.blockSignals(False)

    def _on_sidebar_conversation_clicked(self, item: QListWidgetItem) -> None:
        cid = item.data(Qt.ItemDataRole.UserRole)
        if not cid or self._controller is None:
            return
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._switch_and_focus_chat(str(cid)))

    async def _switch_and_focus_chat(self, conversation_id: str) -> None:
        if self._controller is not None:
            if await self._controller.switch_conversation(conversation_id):
                self.nav.setCurrentRow(0)

    def _make_settings_page(self) -> QWidget:
        view = SettingsView(
            self._state,
            voice_controller=self._voice,
            vision_controller=self._vision,
            preferences_service=self._preferences,
        )
        self.voice_controls = view.voice_controls
        self.vision_controls = view.vision_controls
        return view

    def _refresh_providers(self) -> None:
        if self._controller is None:
            return
        # Repeated clicks are safe: the controller keeps at most one
        # refresh in flight and never polls in the background.
        self._controller.refresh_provider_status()

    # -- Window lifecycle ---------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._controller is not None:
            self._controller.on_workspace_shown()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._closed_emitted:
            self._closed_emitted = True
            self.closed.emit()
        super().closeEvent(event)
