"""Premium native Workspace chat view (Stage 2.6).

Rendering is plain text only (selectable QLabels / QPlainTextEdit); no HTML,
Markdown, or web engine is involved. All state comes from ChatController,
which in turn is fed by the core EventBus through the EventBridge.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.common.orb import OrbWidget
from jarvis.desktop.theme import theme
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.desktop.viewmodels.chat_models import ConversationMessage
from jarvis.desktop.views.consent_overlay import ConsentOverlay
from jarvis.desktop.views.message_bubble import MessageBubble

_STATE_COLORS = {
    "idle": theme.ACCENT_DIM,
    "thinking": theme.ACCENT_WARN,
    "speaking": "#7aa7ff",
    "kill_switch": theme.ACCENT_DANGER,
}


class ComposerEdit(QTextEdit):
    """Multiline composer: Enter sends, Shift+Enter inserts a newline."""

    send_requested = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            event.accept()
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class ChatView(QWidget):
    """Chat surface: welcome, bubbles, streaming, composer, consent overlay."""

    def __init__(
        self,
        state: AppState,
        controller: ChatController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ChatRoot")
        self._state = state
        self._controller = controller
        self._bubbles: dict[str, MessageBubble] = {}
        self._rows: list[QWidget] = []
        self._provider_statuses = (
            controller.provider_statuses if controller is not None else {}
        )

        self._build_ui()
        self._connect_signals()
        self._rebuild_messages()
        self._update_status()
        if (
            self._controller is not None
            and self._controller.current_consent is not None
        ):
            self._show_consent(self._controller.current_consent)

    # -- Public helpers (tests/diagnostics) --------------------------------------

    @property
    def bubbles(self) -> dict[str, MessageBubble]:
        return dict(self._bubbles)

    @property
    def composer(self) -> ComposerEdit:
        return self.input

    @property
    def is_welcome_visible(self) -> bool:
        return self.content_stack.currentIndex() == 0

    # -- UI construction ----------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self._build_welcome())
        self.content_stack.addWidget(self._build_messages())
        root.addWidget(self.content_stack, 1)

        latest_row = QHBoxLayout()
        latest_row.setContentsMargins(0, 0, 26, 4)
        latest_row.addStretch(1)
        self.latest_button = QPushButton("↓ Latest")
        self.latest_button.setObjectName("LatestButton")
        self.latest_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.latest_button.clicked.connect(self._scroll_to_bottom)
        self.latest_button.hide()
        latest_row.addWidget(self.latest_button)
        root.addLayout(latest_row)

        root.addWidget(self._build_composer())

        self.consent_overlay = ConsentOverlay(self)
        self.consent_overlay.approved.connect(self._on_consent_approved)
        self.consent_overlay.denied.connect(self._on_consent_denied)
        self.consent_overlay.setGeometry(self.rect())

        self._escape_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Escape), self
        )
        self._escape_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self._escape_shortcut.activated.connect(self._on_escape)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("ChatHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(28, 16, 28, 14)
        layout.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("Workspace Chat")
        title.setObjectName("ChatTitle")
        subtitle = QLabel("LOCAL-FIRST INTELLIGENCE")
        subtitle.setObjectName("ChatSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        layout.addLayout(titles)
        layout.addStretch(1)

        self.assistant_dot = QLabel("●")
        self.assistant_dot.setObjectName("StateDot")
        self.assistant_status = QLabel("IDLE")
        self.assistant_status.setObjectName("AssistantStatus")
        self.provider_pill = QLabel("LOCAL")
        self.provider_pill.setObjectName("ProviderPill")
        self.kill_pill = QLabel("KILL SWITCH")
        self.kill_pill.setObjectName("DangerPill")
        self.kill_pill.hide()
        layout.addWidget(self.assistant_dot)
        layout.addWidget(self.assistant_status)
        layout.addSpacing(6)
        layout.addWidget(self.provider_pill)
        layout.addWidget(self.kill_pill)
        return header

    def _build_welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(10)
        layout.addStretch(1)

        self.welcome_orb = OrbWidget()
        self.welcome_orb.setFixedSize(150, 150)
        orb_row = QHBoxLayout()
        orb_row.addStretch(1)
        orb_row.addWidget(self.welcome_orb)
        orb_row.addStretch(1)
        layout.addLayout(orb_row)

        title = QLabel("J A R V I S")
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            "Ask anything. Your conversation stays on this machine unless "
            "you explicitly approve a cloud request."
        )
        subtitle.setObjectName("WelcomeSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        self.welcome_hint = QLabel("LOCAL MODEL · Enter to send")
        self.welcome_hint.setObjectName("WelcomeHint")
        self.welcome_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.welcome_hint)
        layout.addStretch(1)
        return page

    def _build_messages(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ChatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.messages_container = QWidget()
        self.messages_container.setObjectName("MessagesContainer")
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(28, 18, 28, 18)
        self.messages_layout.setSpacing(12)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(self.messages_container)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scrolled)

        layout.addWidget(self.scroll)
        return page

    def _build_composer(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("ComposerPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 10, 24, 14)
        layout.setSpacing(8)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ComposerError")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.input = ComposerEdit()
        self.input.setObjectName("ComposerInput")
        self.input.setAcceptRichText(False)
        self.input.setFixedHeight(84)
        self.input.setPlaceholderText("Message JARVIS…")
        layout.addWidget(self.input)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.hint_label = QLabel("Enter to send · Shift+Enter for a new line")
        self.hint_label.setObjectName("ComposerHint")
        row.addWidget(self.hint_label)
        row.addStretch(1)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.hide()
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("AccentButton")
        row.addWidget(self.stop_button)
        row.addWidget(self.send_button)
        layout.addLayout(row)

        if self._controller is None:
            self.input.setEnabled(False)
            self.input.setPlaceholderText("Chat service unavailable")
            self.send_button.setEnabled(False)
            self.error_label.setText("Chat service is unavailable.")
            self.error_label.show()
        return panel

    # -- Wiring --------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.input.textChanged.connect(self._update_send_enabled)
        self.input.send_requested.connect(self._send)
        self.send_button.clicked.connect(self._send)
        self.stop_button.clicked.connect(self._stop)

        self._state.assistant_state_changed.connect(
            lambda _state: self._update_status()
        )
        self._state.kill_switch_changed.connect(
            lambda _active: self._update_status()
        )
        self._state.engine_state_changed.connect(
            lambda _state: self._update_status()
        )
        self._state.provider_health_changed.connect(
            lambda _p, _a, _d: self._update_status()
        )

        if self._controller is None:
            return
        self._controller.messages_changed.connect(self._rebuild_messages)
        self._controller.conversation_reset.connect(self._rebuild_messages)
        self._controller.message_updated.connect(self._on_message_updated)
        self._controller.busy_changed.connect(self._on_busy)
        self._controller.status_changed.connect(self._update_status)
        self._controller.consent_requested.connect(self._show_consent)
        self._controller.consent_dismissed.connect(self._hide_consent)
        self._controller.provider_status_changed.connect(
            self._on_provider_status
        )
        self._controller.error_occurred.connect(self._show_error)

    # -- Composer actions -----------------------------------------------------------

    def _send(self) -> None:
        if self._controller is None:
            return
        text = self.input.toPlainText()
        if not self._controller.can_send(text):
            self._update_send_enabled()
            return
        request_id = self._controller.send(text)
        if request_id is not None:
            self.input.clear()
            self.error_label.hide()
            self._scroll_to_bottom()

    def _stop(self) -> None:
        if self._controller is None:
            return
        if self._controller.stop():
            self.stop_button.setText("Stopping…")
            self.stop_button.setEnabled(False)

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def _update_send_enabled(self) -> None:
        if self._controller is None:
            self.send_button.setEnabled(False)
            return
        self.send_button.setEnabled(
            self._controller.can_send(self.input.toPlainText())
        )

    def _on_busy(self, busy: bool) -> None:
        self.stop_button.setVisible(busy)
        if busy:
            self.stop_button.setText("Stop")
            self.stop_button.setEnabled(True)
        self._update_send_enabled()

    # -- Rendering -------------------------------------------------------------------

    def _rebuild_messages(self) -> None:
        for row in self._rows:
            self.messages_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self._bubbles.clear()

        messages = self._controller.messages if self._controller else ()
        if not messages:
            self.content_stack.setCurrentIndex(0)
            self.latest_button.hide()
            self._update_send_enabled()
            return

        self.content_stack.setCurrentIndex(1)
        insert_at = self.messages_layout.count() - 1  # before the stretch
        width = self._bubble_width()
        for message in messages:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            bubble = MessageBubble(message)
            bubble.set_max_width(width)
            if message.role == "user":
                row_layout.addStretch(1)
                row_layout.addWidget(bubble)
            else:
                row_layout.addWidget(bubble)
                row_layout.addStretch(1)
            self.messages_layout.insertWidget(insert_at, row)
            insert_at += 1
            self._rows.append(row)
            self._bubbles[message.id] = bubble
        self._scroll_to_bottom()
        self._update_send_enabled()

    def _on_message_updated(self, message: ConversationMessage) -> None:
        bubble = self._bubbles.get(message.id)
        near_bottom = self._is_near_bottom()
        if bubble is None:
            self._rebuild_messages()
            return
        bubble.set_max_width(self._bubble_width())
        bubble.update_message(message)
        if near_bottom:
            self._scroll_to_bottom()
        elif self._bubbles:
            self.latest_button.show()

    def _bubble_width(self) -> int:
        viewport = getattr(self, "scroll", None)
        width = viewport.viewport().width() if viewport is not None else 0
        if width <= 0:
            return 560
        return int(width * 0.74)

    # -- Scrolling -------------------------------------------------------------------

    def _is_near_bottom(self) -> bool:
        scroll = getattr(self, "scroll", None)
        if scroll is None:
            return True
        bar = scroll.verticalScrollBar()
        return bar.maximum() - bar.value() <= 48

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(0, self._do_scroll_to_bottom)

    def _do_scroll_to_bottom(self) -> None:
        # The single-shot timer may fire after the owning window was closed
        # and destroyed (close ≠ shutdown); a deleted C++ object must be a
        # harmless no-op, never an exception on the Qt loop.
        try:
            scroll = getattr(self, "scroll", None)
            if scroll is None:
                return
            bar = scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            self.latest_button.hide()
        except RuntimeError:
            return

    def _on_scrolled(self, _value: int) -> None:
        if not self._bubbles:
            self.latest_button.hide()
            return
        self.latest_button.setVisible(not self._is_near_bottom())

    # -- Status ----------------------------------------------------------------------

    def _update_status(self) -> None:
        state_name = self._state.assistant_state or "idle"
        if self._state.kill_switch_active:
            state_name = "kill_switch"
        color = _STATE_COLORS.get(state_name, theme.TEXT_MUTED)
        self.assistant_dot.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.assistant_status.setText(state_name.replace("_", " ").upper())
        self.kill_pill.setVisible(self._state.kill_switch_active)

        if self._controller is None:
            self.provider_pill.setText("LOCAL · unavailable")
            self.provider_pill.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; border-color: {theme.BORDER};"
            )
            return

        provider = self._controller.active_provider
        model = self._controller.active_model
        privacy = self._controller.active_privacy
        if provider:
            parts = [privacy.upper() or "MODEL", provider]
            if model:
                parts.append(model)
            label = " · ".join(parts)
            accent = (
                theme.ACCENT_WARN
                if privacy == "cloud"
                else theme.ACCENT
            )
        else:
            local = self._provider_statuses.get("ollama")
            if local is not None:
                label = f"LOCAL · {local.model or 'ollama'}"
                accent = theme.ACCENT if local.available else theme.TEXT_MUTED
                self.welcome_hint.setText(
                    f"LOCAL MODEL · {local.model or 'ollama'} · Enter to send"
                )
            else:
                label = "LOCAL · not checked"
                accent = theme.TEXT_MUTED
        self.provider_pill.setText(label)
        self.provider_pill.setStyleSheet(
            f"color: {accent}; border: 1px solid {accent}; "
            "border-radius: 9px; padding: 2px 9px; font-size: 10px;"
        )
        self._update_send_enabled()

    def _on_provider_status(self, statuses) -> None:
        self._provider_statuses = dict(statuses)
        self._update_status()

    # -- Consent ---------------------------------------------------------------------

    def _show_consent(self, request) -> None:
        self.consent_overlay.setGeometry(self.rect())
        self.consent_overlay.show_request(request)
        self.consent_overlay.raise_()

    def _hide_consent(self) -> None:
        self.consent_overlay.hide_request()

    def _on_consent_approved(self, request_id: str, fingerprint: str) -> None:
        if self._controller is None:
            return
        if not self._controller.approve_consent(request_id, fingerprint):
            self._hide_consent()

    def _on_consent_denied(self, request_id: str) -> None:
        if self._controller is None:
            return
        self._controller.deny_consent(request_id)
        self._hide_consent()

    def _on_escape(self) -> None:
        if self.consent_overlay.isVisible():
            self.consent_overlay.request_deny()

    # -- Geometry --------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "consent_overlay"):
            self.consent_overlay.setGeometry(self.rect())
        if hasattr(self, "scroll"):
            width = self._bubble_width()
            for bubble in self._bubbles.values():
                bubble.set_max_width(width)
