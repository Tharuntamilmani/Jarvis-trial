"""Dependency-aware DAG TaskPlanner with WorkingMemory & checkpoints (Phase 4.2)."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Awaitable, Callable

from jarvis.agents.base_agent import AgentError
from jarvis.agents.manager import AgentManager
from jarvis.agents.models import AgentStatus
from jarvis.agents.plan_models import (
    ExecutionPlan,
    PlanCheckpoint,
    PlanResult,
    PlanStep,
    PlanStepStatus,
)
from jarvis.memory.working_memory import WorkingMemory


class PlanValidationError(AgentError):
    """Raised when an execution plan is malformed, cyclic, or over budget."""


class PlanBudgetError(AgentError):
    """Raised when plan execution exceeds its configured budget."""


class PlanApprovalRequiredError(AgentError):
    """Raised when a step requires human approval and is denied/unavailable."""


ApprovalCallback = Callable[[PlanStep], Awaitable[bool]]


class TaskPlanner:
    """Validates and executes ``ExecutionPlan`` DAGs through ``AgentManager``."""

    def __init__(self, manager: AgentManager) -> None:
        self._manager = manager

    def validate(self, plan: ExecutionPlan) -> list[PlanStep]:
        """Validate steps, agents, dependencies, budgets, and acyclicity.

        Returns steps in a deterministic topological order.
        """
        if plan.budget.max_steps < 1 or plan.budget.max_concurrency < 1:
            raise PlanValidationError("plan budget limits must be >= 1")
        if not plan.steps:
            raise PlanValidationError("execution plan must contain at least one step")
        if len(plan.steps) > plan.budget.max_steps:
            raise PlanBudgetError(
                f"plan has {len(plan.steps)} steps; max_steps is "
                f"{plan.budget.max_steps}"
            )

        by_id: dict[str, PlanStep] = {}
        for step in plan.steps:
            sid = step.step_id.strip()
            if not sid:
                raise PlanValidationError("step_id cannot be empty")
            if sid in by_id:
                raise PlanValidationError(f"duplicate step_id: {sid!r}")
            if not self._manager.registry.has(step.agent_id):
                raise PlanValidationError(
                    f"unknown agent {step.agent_id!r} for step {sid!r}"
                )
            by_id[sid] = step

        in_degree: dict[str, int] = {sid: 0 for sid in by_id}
        dependents: dict[str, list[str]] = {sid: [] for sid in by_id}
        for sid, step in by_id.items():
            for dep in step.depends_on:
                if dep not in by_id:
                    raise PlanValidationError(
                        f"step {sid!r} depends on missing step {dep!r}"
                    )
                if dep == sid:
                    raise PlanValidationError(f"step {sid!r} depends on itself")
                in_degree[sid] += 1
                dependents[dep].append(sid)

        queue = deque(sid for sid, deg in in_degree.items() if deg == 0)
        ordered: list[PlanStep] = []
        while queue:
            sid = queue.popleft()
            ordered.append(by_id[sid])
            for nxt in dependents[sid]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(ordered) != len(by_id):
            raise PlanValidationError("execution plan contains a dependency cycle")
        return ordered

    def preview(self, plan: ExecutionPlan) -> str:
        """Return a human-readable topological preview before execution."""
        ordered = self.validate(plan)
        lines = [f"Plan {plan.plan_id}: {plan.goal} ({len(ordered)} steps)"]
        for idx, step in enumerate(ordered, start=1):
            deps = ", ".join(step.depends_on) if step.depends_on else "none"
            gate = " [APPROVAL REQUIRED]" if step.requires_approval else ""
            lines.append(
                f"{idx}. {step.step_id} -> {step.agent_id} "
                f"(deps: {deps}){gate}"
            )
        return "\n".join(lines)

    async def execute(
        self,
        plan: ExecutionPlan,
        *,
        working_memory: WorkingMemory | None = None,
        parent_run_id: str | None = None,
        approve_fn: ApprovalCallback | None = None,
        checkpoint: PlanCheckpoint | None = None,
    ) -> PlanResult:
        """Execute a validated DAG within budget, storing outputs in WorkingMemory."""
        ordered = self.validate(plan)
        wm = working_memory if working_memory is not None else WorkingMemory()
        cp = checkpoint if checkpoint is not None else PlanCheckpoint(plan.plan_id)

        outputs: dict[str, str] = dict(cp.completed_outputs)
        statuses: dict[str, PlanStepStatus] = {
            s.step_id: cp.step_statuses.get(s.step_id, PlanStepStatus.PENDING)
            for s in ordered
        }
        for sid, out in outputs.items():
            wm.set(f"plan.{plan.plan_id}.step.{sid}.output", out)

        by_id = {s.step_id: s for s in ordered}
        active_tasks: dict[str, asyncio.Task[tuple[str, PlanStepStatus, str]]] = {}
        active_run_ids: dict[str, str] = {}
        sem = asyncio.Semaphore(plan.budget.max_concurrency)
        failed_error: str | None = None
        total_output_chars = sum(len(v) for v in outputs.values())

        async def _run_step(step: PlanStep) -> tuple[str, PlanStepStatus, str]:
            async with sem:
                if step.requires_approval:
                    approved = (
                        await approve_fn(step) if approve_fn is not None else False
                    )
                    if not approved:
                        raise PlanApprovalRequiredError(
                            f"step {step.step_id!r} requires approval"
                        )
                dep_outputs = {
                    dep: outputs.get(dep, "") for dep in step.depends_on
                }
                attempts = 1 + (max(0, step.max_retries) if step.retryable else 0)
                last_status = PlanStepStatus.FAILED
                last_output = ""
                for attempt in range(attempts):
                    idem = f"plan:{plan.plan_id}:{step.step_id}:a{attempt}"
                    rid = await self._manager.submit(
                        step.agent_id,
                        step.input_text,
                        idempotency_key=idem,
                        parent_run_id=parent_run_id,
                        metadata={
                            "plan_id": plan.plan_id,
                            "step_id": step.step_id,
                            "dependency_outputs": dep_outputs,
                        },
                    )
                    active_run_ids[step.step_id] = rid
                    try:
                        rec = await self._manager.wait(rid)
                    finally:
                        active_run_ids.pop(step.step_id, None)

                    if rec.status is AgentStatus.COMPLETED:
                        return (
                            step.step_id,
                            PlanStepStatus.COMPLETED,
                            rec.output or "",
                        )
                    if rec.status is AgentStatus.CANCELLED:
                        return (
                            step.step_id,
                            PlanStepStatus.CANCELLED,
                            rec.output or "",
                        )
                    last_status = PlanStepStatus.FAILED
                    last_output = rec.output or ""
                return step.step_id, last_status, last_output

        try:
            async with asyncio.timeout(plan.budget.max_duration_seconds):
                while True:
                    # Launch all ready pending steps when no failure has occurred.
                    if failed_error is None:
                        for step in ordered:
                            sid = step.step_id
                            if statuses[sid] is not PlanStepStatus.PENDING:
                                continue
                            if all(
                                statuses[dep] is PlanStepStatus.COMPLETED
                                for dep in step.depends_on
                            ):
                                statuses[sid] = PlanStepStatus.RUNNING
                                active_tasks[sid] = asyncio.create_task(
                                    _run_step(step)
                                )

                    if not active_tasks:
                        break

                    done, _ = await asyncio.wait(
                        active_tasks.values(),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        finished_sid = next(
                            k for k, v in active_tasks.items() if v is task
                        )
                        active_tasks.pop(finished_sid)
                        try:
                            sid, st, out = task.result()
                        except PlanApprovalRequiredError as exc:
                            statuses[finished_sid] = PlanStepStatus.FAILED
                            cp.step_statuses[finished_sid] = PlanStepStatus.FAILED
                            failed_error = str(exc)
                            break
                        except asyncio.CancelledError:
                            statuses[finished_sid] = PlanStepStatus.CANCELLED
                            cp.step_statuses[finished_sid] = (
                                PlanStepStatus.CANCELLED
                            )
                            raise
                        except Exception as exc:  # noqa: BLE001
                            statuses[finished_sid] = PlanStepStatus.FAILED
                            cp.step_statuses[finished_sid] = PlanStepStatus.FAILED
                            failed_error = f"step {finished_sid!r} failed: {exc}"
                            break

                        statuses[sid] = st
                        cp.step_statuses[sid] = st
                        if st is PlanStepStatus.COMPLETED:
                            remaining = max(
                                0, plan.budget.max_output_chars - total_output_chars
                            )
                            bounded = out[:remaining]
                            total_output_chars += len(bounded)
                            outputs[sid] = bounded
                            cp.completed_outputs[sid] = bounded
                            wm.set(
                                f"plan.{plan.plan_id}.step.{sid}.output",
                                bounded,
                            )
                        else:
                            failed_error = f"step {sid!r} ended with {st.value}"
                            break

                    if failed_error is not None:
                        await _cancel_active(
                            active_tasks, active_run_ids, self._manager
                        )
                        break
        except TimeoutError:
            failed_error = "plan execution timed out"
            await _cancel_active(active_tasks, active_run_ids, self._manager)
        except asyncio.CancelledError:
            await _cancel_active(active_tasks, active_run_ids, self._manager)
            raise

        for step in by_id.values():
            if statuses[step.step_id] is PlanStepStatus.PENDING:
                statuses[step.step_id] = PlanStepStatus.SKIPPED
                cp.step_statuses[step.step_id] = PlanStepStatus.SKIPPED

        final_status = (
            PlanStepStatus.COMPLETED
            if failed_error is None
            and all(s is PlanStepStatus.COMPLETED for s in statuses.values())
            else PlanStepStatus.FAILED
        )
        return PlanResult(
            plan_id=plan.plan_id,
            status=final_status,
            step_outputs=outputs,
            step_statuses=statuses,
            error=failed_error,
        )


async def _cancel_active(
    active_tasks: dict[str, asyncio.Task],
    active_run_ids: dict[str, str],
    manager: AgentManager,
) -> None:
    for rid in list(active_run_ids.values()):
        with contextlib.suppress(BaseException):
            await manager.cancel(rid)
    for task in list(active_tasks.values()):
        if not task.done():
            task.cancel()
    if active_tasks:
        with contextlib.suppress(BaseException):
            await asyncio.gather(*active_tasks.values(), return_exceptions=True)
    active_tasks.clear()
