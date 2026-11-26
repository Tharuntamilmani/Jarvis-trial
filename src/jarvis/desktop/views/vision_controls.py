"""Native PySide6 camera, biometrics & gesture controls panel (Phase 6.4)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.vision_controller import VisionController


class VisionControlsPanel(QFrame):
    """Visible camera privacy indicator, device selector, face & gesture toggles."""

    def __init__(
        self,
        state: AppState,
        controller: VisionController | None = None,
        *,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("GlassPanel")
        self._state = state
        self._controller = controller
        self._compact = compact
        self._build_ui()
        self._connect_state()
        self._sync_from_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        margin = 10 if self._compact else 18
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(6 if self._compact else 12)

        header = QHBoxLayout()
        title = QLabel("VISION & BIOMETRICS" if not self._compact else "VISION")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.camera_status_label = QLabel("CAMERA OFF")
        self.camera_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.camera_status_label)
        layout.addLayout(header)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #ff4d5e; font-size: 11px;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        if not self._compact:
            dev_row = QHBoxLayout()
            dev_row.addWidget(QLabel("Camera"))
            self.device_combo = QComboBox()
            self.device_combo.currentIndexChanged.connect(self._on_device_selected)
            dev_row.addWidget(self.device_combo, 1)
            self.refresh_btn = QPushButton("Refresh")
            self.refresh_btn.clicked.connect(self._on_refresh_devices)
            dev_row.addWidget(self.refresh_btn)
            layout.addLayout(dev_row)
        else:
            self.device_combo = None
            self.refresh_btn = None

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.camera_btn = QPushButton("Camera: Off")
        self.camera_btn.setCheckable(True)
        self.camera_btn.toggled.connect(self._on_camera_toggled)
        btn_row.addWidget(self.camera_btn)

        self.face_btn = QPushButton("Face: Off" if self._compact else "Face Recognition: Off")
        self.face_btn.setCheckable(True)
        self.face_btn.toggled.connect(self._on_face_toggled)
        btn_row.addWidget(self.face_btn)

        self.gesture_btn = QPushButton(
            "Gestures: Off" if self._compact else "Gesture Controls: Off"
        )
        self.gesture_btn.setCheckable(True)
        self.gesture_btn.toggled.connect(self._on_gesture_toggled)
        btn_row.addWidget(self.gesture_btn)

        layout.addLayout(btn_row)

        self.user_label = QLabel("")
        self.user_label.setStyleSheet("color: #7f95b2; font-size: 11px;")
        self.user_label.setVisible(False)
        layout.addWidget(self.user_label)

    def _connect_state(self) -> None:
        self._state.vision_state_changed.connect(lambda *_: self._sync_status())
        self._state.vision_devices_changed.connect(lambda *_: self._sync_devices())
        self._state.vision_error_changed.connect(lambda *_: self._sync_status())
        self._state.vision_user_recognized.connect(
            lambda *_: self._sync_status()
        )

    def _sync_from_state(self) -> None:
        self._sync_devices()
        self._sync_status()

    def _sync_devices(self) -> None:
        if self.device_combo is None:
            return
        self.device_combo.blockSignals(True)
        try:
            self.device_combo.clear()
            if not self._state.vision_devices:
                self.device_combo.addItem("No camera detected", "")
                self.device_combo.setEnabled(False)
                return
            self.device_combo.setEnabled(True)
            selected_idx = 0
            for idx, dev in enumerate(self._state.vision_devices):
                name = str(dev.get("device_name") or dev.get("device_id") or "Camera")
                dev_id = str(dev.get("device_id") or "")
                self.device_combo.addItem(name, dev_id)
                if dev_id == self._state.vision_selected_device:
                    selected_idx = idx
            self.device_combo.setCurrentIndex(selected_idx)
        finally:
            self.device_combo.blockSignals(False)

    def _sync_status(self) -> None:
        if not self._state.vision_camera_available:
            self.camera_status_label.setText("CAMERA UNAVAILABLE")
            self.camera_status_label.setStyleSheet(
                "color: #ff4d5e; font-weight: bold; font-size: 11px;"
            )
        elif self._state.vision_camera_active:
            self.camera_status_label.setText("CAMERA ACTIVE")
            self.camera_status_label.setStyleSheet(
                "color: #39d98a; font-weight: bold; font-size: 11px;"
            )
        elif self._state.vision_state == "locked":
            self.camera_status_label.setText("VISION LOCKED")
            self.camera_status_label.setStyleSheet(
                "color: #ffb020; font-weight: bold; font-size: 11px;"
            )
        else:
            self.camera_status_label.setText("CAMERA OFF")
            self.camera_status_label.setStyleSheet("color: #7f95b2; font-size: 11px;")

        err = self._state.vision_error_detail
        self.error_label.setText(err)
        self.error_label.setVisible(bool(err))

        self.camera_btn.blockSignals(True)
        self.camera_btn.setChecked(self._state.vision_camera_active)
        self.camera_btn.setText(
            f"Camera: {'On' if self._state.vision_camera_active else 'Off'}"
        )
        self.camera_btn.blockSignals(False)

        self.face_btn.blockSignals(True)
        self.face_btn.setChecked(self._state.vision_face_enabled)
        face_prefix = "Face" if self._compact else "Face Recognition"
        self.face_btn.setText(
            f"{face_prefix}: {'On' if self._state.vision_face_enabled else 'Off'}"
        )
        self.face_btn.blockSignals(False)

        self.gesture_btn.blockSignals(True)
        self.gesture_btn.setChecked(self._state.vision_gesture_enabled)
        gest_prefix = "Gestures" if self._compact else "Gesture Controls"
        self.gesture_btn.setText(
            f"{gest_prefix}: {'On' if self._state.vision_gesture_enabled else 'Off'}"
        )
        self.gesture_btn.blockSignals(False)

        if self._state.vision_last_user:
            self.user_label.setText(f"Recognized: {self._state.vision_last_user}")
            self.user_label.setVisible(True)

    # -- Callbacks -------------------------------------------------------------

    def _on_refresh_devices(self) -> None:
        if self._controller is not None:
            self._controller.refresh_devices()

    def _on_device_selected(self, index: int) -> None:
        if self.device_combo is None or self._controller is None or index < 0:
            return
        dev_id = self.device_combo.itemData(index)
        if dev_id:
            self._controller.select_device(str(dev_id))

    def _on_camera_toggled(self, checked: bool) -> None:
        if self._controller is not None:
            self._controller.set_camera_enabled(checked)

    def _on_face_toggled(self, checked: bool) -> None:
        if self._controller is not None:
            self._controller.set_face_recognition_enabled(checked)

    def _on_gesture_toggled(self, checked: bool) -> None:
        if self._controller is not None:
            self._controller.set_gesture_recognition_enabled(checked)
