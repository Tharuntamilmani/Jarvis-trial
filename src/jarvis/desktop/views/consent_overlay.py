"""Inline cloud-consent card (never a modal dialog).

The card displays exactly the sanitized payload the router validated:
the approved static system prompt and the current user message. Closing,
dismissing, rejecting, or pressing Escape always denies.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.viewmodels.chat_models import ConsentRequest


class ConsentOverlay(QFrame):
    """Full-area backdrop with a centered, scrollable consent card."""

    approved = Signal(str, str)  # request_id, fingerprint
    denied = Signal(str)  # request_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConsentBackdrop")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._request: ConsentRequest | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.addStretch(1)

        card_row = QHBoxLayout()
        card_row.addStretch(1)
        card = QFrame()
        card.setObjectName("ConsentCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setMaximumWidth(680)
        card_row.addWidget(card, 4)
        card_row.addStretch(1)
        root.addLayout(card_row)
        root.addStretch(1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)

        title = QLabel("CLOUD CONSENT REQUIRED")
        title.setObjectName("ConsentTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "The local model is unavailable. Approving sends ONLY the two "
            "messages below to the cloud provider. Nothing else leaves this "
            "machine."
        )
        subtitle.setObjectName("ConsentSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        info = QGridLayout()
        info.setHorizontalSpacing(14)
        info.setVerticalSpacing(4)
        self._provider = QLabel("—")
        self._model = QLabel("—")
        self._reason = QLabel("—")
        self._scope = QLabel("—")
        for row, (name, value) in enumerate(
            (
                ("Provider", self._provider),
                ("Model", self._model),
                ("Reason", self._reason),
                ("Scope", self._scope),
            )
        ):
            key = QLabel(name.upper())
            key.setObjectName("ConsentKey")
            value.setObjectName("ConsentValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            info.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
            info.addWidget(value, row, 1)
        info.setColumnStretch(1, 1)
        layout.addLayout(info)

        system_title = QLabel("APPROVED SYSTEM PROMPT (EXACT)")
        system_title.setObjectName("ConsentKey")
        layout.addWidget(system_title)
        self._system_prompt = self._readonly_text(height=104)
        layout.addWidget(self._system_prompt)

        message_title = QLabel("YOUR MESSAGE (EXACT)")
        message_title.setObjectName("ConsentKey")
        layout.addWidget(message_title)
        self._user_message = self._readonly_text(height=64)
        layout.addWidget(self._user_message)

        note = QLabel(
            "Excluded by construction: conversation history, memory, files, "
            "attachments, tool output, retrieved documents, and machine "
            "details."
        )
        note.setObjectName("ConsentNote")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("GhostButton")
        self.reject_button = QPushButton("Reject")
        self.reject_button.setObjectName("DangerButton")
        self.approve_button = QPushButton("Approve cloud request")
        self.approve_button.setObjectName("AccentButton")
        buttons.addWidget(self.close_button)
        buttons.addStretch(1)
        buttons.addWidget(self.reject_button)
        buttons.addWidget(self.approve_button)
        layout.addLayout(buttons)

        self.close_button.clicked.connect(self._deny)
        self.reject_button.clicked.connect(self._deny)
        self.approve_button.clicked.connect(self._approve)

        self.hide()

    # -- API --------------------------------------------------------------------

    def show_request(self, request: ConsentRequest) -> None:
        self._request = request
        self._provider.setText(request.provider or "unknown")
        self._model.setText(request.model or "provider default")
        self._reason.setText(request.reason or "local model unavailable")
        self._scope.setText(request.scope or "system prompt + current message")
        self._system_prompt.setPlainText(request.system_prompt)
        self._user_message.setPlainText(request.user_message)
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.reject_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def hide_request(self) -> None:
        self._request = None
        self.hide()

    def request_deny(self) -> None:
        self._deny()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self._deny()
            return
        super().keyPressEvent(event)

    # -- Internals ----------------------------------------------------------------

    @staticmethod
    def _readonly_text(height: int) -> QPlainTextEdit:
        widget = QPlainTextEdit()
        widget.setObjectName("ConsentText")
        widget.setReadOnly(True)
        widget.setTabChangesFocus(True)
        widget.setFixedHeight(height)
        widget.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        return widget

    def _approve(self) -> None:
        request = self._request
        if request is None:
            return
        self.approved.emit(request.request_id, request.fingerprint)
        self.hide_request()

    def _deny(self) -> None:
        request = self._request
        if request is None:
            self.hide()
            return
        request_id = request.request_id
        self.denied.emit(request_id)
        self.hide_request()
