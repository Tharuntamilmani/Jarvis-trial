"""Native Workspace Agents view (Phase 4.5)."""

from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class AgentsView(QWidget):
    """Displays active runs, run history, scheduled jobs, and cancel controls."""

    def __init__(
        self,
        state: AppState,
        agent_manager: Any | None = None,
        scheduler: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._manager = agent_manager
        self._scheduler = scheduler
        self._tasks: set[asyncio.Task[Any]] = set()

        self._build_ui()
        self._connect_state()
        self.refresh_live()

    def set_services(
        self, agent_manager: Any | None, scheduler: Any | None = None
    ) -> None:
        self._manager = agent_manager
        self._scheduler = scheduler
        self.refresh_live()
        self._spawn(self.refresh_history())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        hdr = QHBoxLayout()
        title = QLabel("Agents")
        title.setObjectName("SectionTitle")
        hdr.addWidget(title)
        hdr.addStretch(1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("GhostButton")
        self.refresh_button.clicked.connect(
            lambda: self._spawn(self.refresh_history())
        )
        hdr.addWidget(self.refresh_button)
        layout.addLayout(hdr)

        active_lbl = QLabel("ACTIVE RUNS")
        active_lbl.setObjectName("SidebarCaption")
        layout.addWidget(active_lbl)
        self.active_list = QListWidget()
        self.active_list.setMaximumHeight(140)
        self.active_list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.active_list)

        act_row = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel Run")
        self.cancel_button.setObjectName("DangerButton")
        self.cancel_button.clicked.connect(self._on_cancel)
        act_row.addWidget(self.cancel_button)
        act_row.addStretch(1)
        layout.addLayout(act_row)

        hist_lbl = QLabel("EXECUTION HISTORY")
        hist_lbl.setObjectName("SidebarCaption")
        layout.addWidget(hist_lbl)
        self.history_list = QListWidget()
        layout.addWidget(self.history_list, 1)

        sched_lbl = QLabel("SCHEDULED JOBS")
        sched_lbl.setObjectName("SidebarCaption")
        layout.addWidget(sched_lbl)
        self.jobs_list = QListWidget()
        self.jobs_list.setMaximumHeight(110)
        layout.addWidget(self.jobs_list)

        self._update_buttons()

    def _connect_state(self) -> None:
        self._state.agent_run_started.connect(lambda *_: self.refresh_live())
        self._state.agent_step_updated.connect(lambda *_: self.refresh_live())
        self._state.agent_run_finished.connect(lambda *_: self.refresh_live())

    def refresh_live(self) -> None:
        self.active_list.clear()
        for rid, info in self._state.active_agents.items():
            step = info.get("step", "running")
            pct = int(float(info.get("progress", 0.0)) * 100)
            item = QListWidgetItem(
                f"{info.get('agent_id', 'agent')} · {step} ({pct}%) [{rid[:8]}]",
                self.active_list,
            )
            item.setData(Qt.ItemDataRole.UserRole, rid)

        self.history_list.clear()
        for entry in self._state.recent_agent_activity:
            err = (
                f" · {entry['error_category']}"
                if entry.get("error_category")
                else ""
            )
            QListWidgetItem(
                f"{entry['agent_id']} · {entry['status']}{err} "
                f"({entry.get('duration_ms', 0)} ms)",
                self.history_list,
            )

        self.jobs_list.clear()
        if self._scheduler is not None:
            for job in self._scheduler.list_jobs():
                QListWidgetItem(
                    f"{job.agent_id} · {job.kind.value} · {job.status.value}",
                    self.jobs_list,
                )
        self._update_buttons()

    async def refresh_history(self) -> None:
        if self._manager is None:
            return
        store = getattr(self._manager, "_store", None)
        if store is None:
            return
        try:
            runs = await store.list_runs(limit=50)
        except Exception:  # noqa: BLE001
            log.warning("failed to load agent run history", exc_info=True)
            return
        self.history_list.clear()
        for run in runs:
            err = f" · {run.error_category}" if run.error_category else ""
            ms = f" ({run.duration_ms} ms)" if run.duration_ms is not None else ""
            QListWidgetItem(
                f"{run.agent} · {run.status.value}{err}{ms}",
                self.history_list,
            )

    def _update_buttons(self) -> None:
        item = self.active_list.currentItem()
        self.cancel_button.setEnabled(
            self._manager is not None and item is not None
        )
        self.refresh_button.setEnabled(self._manager is not None)

    def _on_cancel(self) -> None:
        item = self.active_list.currentItem()
        if item is None or self._manager is None:
            return
        rid = str(item.data(Qt.ItemDataRole.UserRole))
        self._spawn(self._manager.cancel(rid))

    def _spawn(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            return
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
