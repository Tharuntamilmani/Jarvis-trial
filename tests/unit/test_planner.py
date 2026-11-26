"""Unit tests for Phase 4.2 TaskPlanner, DAG execution, and WorkingMemory."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.agents import (
    AgentContext,
    AgentManager,
    AgentRegistry,
    AgentResult,
    BaseAgent,
    ExecutionPlan,
    PlanBudget,
    PlanBudgetError,
    PlanCheckpoint,
    PlanStep,
    PlanStepStatus,
    PlanValidationError,
    TaskPlanner,
)
from jarvis.core.event_bus import EventBus
from jarvis.memory.working_memory import WorkingMemory
from tests.unit.test_agents import _InMemoryRunStore


class _DepEchoAgent(BaseAgent):
    agent_id = "dep_echo"
    name = "DepEcho"

    async def run(self, context: AgentContext) -> AgentResult:
        deps = context.metadata.get("dependency_outputs", {})
        dep_summary = ",".join(f"{k}={v}" for k, v in sorted(deps.items()))
        return AgentResult(output=f"{context.input_text}({dep_summary})")


class _BarrierAgent(BaseAgent):
    agent_id = "barrier"
    name = "Barrier"

    def __init__(self, expected: int = 2) -> None:
        self._expected = expected
        self._count = 0
        self._both_entered = asyncio.Event()
        self.peak = 0

    async def run(self, context: AgentContext) -> AgentResult:
        self._count += 1
        self.peak = max(self.peak, self._count)
        if self._count >= self._expected:
            self._both_entered.set()
        await asyncio.wait_for(self._both_entered.wait(), timeout=2.0)
        self._count -= 1
        return AgentResult(output=context.input_text)


class _FlakyAgent(BaseAgent):
    agent_id = "flaky"
    name = "Flaky"

    def __init__(self, fail_times: int = 1) -> None:
        self.fail_times = fail_times
        self.attempts = 0

    async def run(self, context: AgentContext) -> AgentResult:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("transient error")
        return AgentResult(output="recovered")


class _HoldAgent(BaseAgent):
    agent_id = "hold"
    name = "Hold"

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = False

    async def run(self, context: AgentContext) -> AgentResult:
        self.entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return AgentResult(output="unreachable")


def _planner(*agents: BaseAgent, max_concurrent: int = 4) -> TaskPlanner:
    reg = AgentRegistry()
    for agent in agents:
        reg.register(agent)
    mgr = AgentManager(
        reg,
        _InMemoryRunStore(),  # type: ignore[arg-type]
        EventBus(),
        max_concurrent=max_concurrent,
    )
    return TaskPlanner(mgr)


def test_validate_rejects_cycles_missing_deps_unknown_agent_and_budget() -> None:
    planner = _planner(_DepEchoAgent())

    with pytest.raises(PlanValidationError, match="cycle"):
        planner.validate(
            ExecutionPlan(
                goal="cycle",
                steps=(
                    PlanStep("a", "dep_echo", "1", depends_on=("b",)),
                    PlanStep("b", "dep_echo", "2", depends_on=("a",)),
                ),
            )
        )

    with pytest.raises(PlanValidationError, match="missing step"):
        planner.validate(
            ExecutionPlan(
                goal="missing",
                steps=(PlanStep("a", "dep_echo", "1", depends_on=("nope",)),),
            )
        )

    with pytest.raises(PlanValidationError, match="unknown agent"):
        planner.validate(
            ExecutionPlan(
                goal="unknown",
                steps=(PlanStep("a", "ghost", "1"),),
            )
        )

    with pytest.raises(PlanBudgetError, match="max_steps"):
        planner.validate(
            ExecutionPlan(
                goal="too big",
                steps=(
                    PlanStep("a", "dep_echo", "1"),
                    PlanStep("b", "dep_echo", "2"),
                ),
                budget=PlanBudget(max_steps=1),
            )
        )


async def test_dag_parallel_execution_working_memory_and_preview() -> None:
    barrier = _BarrierAgent(expected=2)
    planner = _planner(barrier, _DepEchoAgent(), max_concurrent=4)
    wm = WorkingMemory()

    plan = ExecutionPlan(
        goal="parallel research",
        steps=(
            PlanStep("s1", "barrier", "left"),
            PlanStep("s2", "barrier", "right"),
            PlanStep("s3", "dep_echo", "merge", depends_on=("s1", "s2")),
        ),
    )
    preview = planner.preview(plan)
    assert "s1 -> barrier" in preview
    assert "s3 -> dep_echo" in preview

    result = await planner.execute(plan, working_memory=wm)
    assert result.status is PlanStepStatus.COMPLETED
    assert barrier.peak == 2
    assert result.step_outputs["s3"] == "merge(s1=left,s2=right)"
    assert wm.get(f"plan.{plan.plan_id}.step.s3.output") == "merge(s1=left,s2=right)"


async def test_bounded_retry_and_failure_stops_downstream() -> None:
    flaky = _FlakyAgent(fail_times=1)
    always_fail = _FlakyAgent(fail_times=5)
    always_fail.agent_id = "always_fail"
    planner = _planner(flaky, always_fail, _DepEchoAgent())

    # Retryable step recovers on attempt 2.
    ok_plan = ExecutionPlan(
        goal="retry",
        steps=(PlanStep("r1", "flaky", "go", retryable=True, max_retries=1),),
    )
    ok_res = await planner.execute(ok_plan)
    assert ok_res.status is PlanStepStatus.COMPLETED
    assert flaky.attempts == 2

    # Failure stops execution and marks downstream step SKIPPED.
    fail_plan = ExecutionPlan(
        goal="fail",
        steps=(
            PlanStep("f1", "always_fail", "boom"),
            PlanStep("f2", "dep_echo", "after", depends_on=("f1",)),
        ),
    )
    fail_res = await planner.execute(fail_plan)
    assert fail_res.status is PlanStepStatus.FAILED
    assert fail_res.step_statuses["f1"] is PlanStepStatus.FAILED
    assert fail_res.step_statuses["f2"] is PlanStepStatus.SKIPPED


async def test_checkpoint_recovery_skips_completed_steps() -> None:
    planner = _planner(_DepEchoAgent())
    plan = ExecutionPlan(
        plan_id="p-resume",
        goal="resume",
        steps=(
            PlanStep("s1", "dep_echo", "first"),
            PlanStep("s2", "dep_echo", "second", depends_on=("s1",)),
        ),
    )
    cp = PlanCheckpoint(
        plan_id="p-resume",
        completed_outputs={"s1": "cached-one"},
        step_statuses={"s1": PlanStepStatus.COMPLETED},
    )
    res = await planner.execute(plan, checkpoint=cp)
    assert res.status is PlanStepStatus.COMPLETED
    assert res.step_outputs["s1"] == "cached-one"
    assert res.step_outputs["s2"] == "second(s1=cached-one)"


async def test_approval_checkpoint_fails_closed_and_cancellation_propagates() -> None:
    hold = _HoldAgent()
    planner = _planner(_DepEchoAgent(), hold)

    # Approval required with no callback fails closed.
    gated_plan = ExecutionPlan(
        goal="gated",
        steps=(PlanStep("g1", "dep_echo", "x", requires_approval=True),),
    )
    res = await planner.execute(gated_plan)
    assert res.status is PlanStepStatus.FAILED
    assert "requires approval" in (res.error or "")

    # Cancelling plan task cancels in-flight child agent run.
    cancel_plan = ExecutionPlan(
        goal="cancel",
        steps=(PlanStep("h1", "hold", "wait"),),
    )
    task = asyncio.create_task(planner.execute(cancel_plan))
    await hold.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hold.cancelled is True
