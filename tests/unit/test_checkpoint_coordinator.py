"""PersistenceCoordinator: throttling, single-flight, staleness, finalisation."""

from __future__ import annotations

import asyncio

from jarvis.conversations.checkpoint import PersistenceCoordinator


async def _settle() -> None:
    for _ in range(4):
        await asyncio.sleep(0)


class Recorder:
    def __init__(self) -> None:
        self.writes: list[tuple[str, int]] = []
        self.finals: list[tuple[str, int, str, str | None, int]] = []
        self.gate: asyncio.Event | None = None
        self.reject = False
        self.final_result = True

    async def checkpoint(self, content: str, version: int) -> int | None:
        if self.gate is not None:
            await self.gate.wait()
        self.writes.append((content, version))
        if self.reject:
            return None
        return version + 1

    async def finalize(
        self,
        content: str,
        version: int,
        status: str,
        error: str | None,
        tokens: int,
    ) -> bool:
        self.finals.append((content, version, status, error, tokens))
        return self.final_result


def _coordinator(recorder: Recorder, *, clock=None, version: int = 0):
    return PersistenceCoordinator(
        checkpoint=recorder.checkpoint,
        finalize=recorder.finalize,
        version=version,
        clock=clock or (lambda: 1000.0),
    )


async def test_no_checkpoint_before_minimum_characters() -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)

    coordinator.observe("a" * 100)
    await _settle()
    assert recorder.writes == []

    coordinator.observe("a" * 300)
    await _settle()
    assert len(recorder.writes) == 1
    assert recorder.writes[0] == ("a" * 300, 0)


async def test_checkpoint_interval_is_respected() -> None:
    recorder = Recorder()
    now = [1000.0]
    coordinator = _coordinator(recorder, clock=lambda: now[0])

    coordinator.observe("a" * 300)
    await _settle()
    assert len(recorder.writes) == 1

    # Same clock instant: the interval has not elapsed.
    coordinator.observe("a" * 700)
    await _settle()
    assert len(recorder.writes) == 1

    now[0] += 1.5
    coordinator.observe("a" * 700)
    await _settle()
    assert len(recorder.writes) == 2
    assert recorder.writes[1][1] == 1


async def test_only_one_checkpoint_in_flight() -> None:
    recorder = Recorder()
    recorder.gate = asyncio.Event()
    coordinator = _coordinator(recorder)

    coordinator.observe("a" * 300)
    await _settle()
    coordinator.observe("a" * 700)
    await _settle()

    assert coordinator.inflight is True
    assert recorder.writes == []  # the first write is still blocked

    recorder.gate.set()
    await _settle()
    assert len(recorder.writes) == 1


async def test_stale_checkpoint_does_not_advance_version() -> None:
    recorder = Recorder()
    recorder.reject = True
    coordinator = _coordinator(recorder)

    coordinator.observe("a" * 300)
    await _settle()

    assert coordinator.stale_rejections == 1
    assert coordinator.version == 0
    assert coordinator.checkpoints_written == 0


async def test_finalize_flushes_final_content_and_reports_success() -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)

    confirmed = await coordinator.close_and_finalize(
        status="completed", content="final answer", tokens=12
    )

    assert confirmed is True
    assert recorder.finals == [("final answer", 0, "completed", None, 12)]
    assert coordinator.terminal is True


async def test_finalize_waits_for_inflight_checkpoint_version() -> None:
    recorder = Recorder()
    recorder.gate = asyncio.Event()
    coordinator = _coordinator(recorder)

    coordinator.observe("a" * 300)
    await _settle()

    task = asyncio.create_task(
        coordinator.close_and_finalize(status="completed", content="final")
    )
    recorder.gate.set()
    await task

    # The checkpoint bumped the version to 1; finalisation must use it.
    assert recorder.finals[0][1] == 1


async def test_no_writes_after_terminal() -> None:
    recorder = Recorder()
    coordinator = _coordinator(recorder)
    await coordinator.close_and_finalize(status="completed", content="done")
    before = len(recorder.writes)

    coordinator.observe("b" * 1000)
    await _settle()

    assert len(recorder.writes) == before


async def test_finalize_failure_is_reported_not_raised() -> None:
    recorder = Recorder()
    recorder.final_result = False
    coordinator = _coordinator(recorder)

    confirmed = await coordinator.close_and_finalize(
        status="failed", content="partial", error="provider_error"
    )

    assert confirmed is False
    assert recorder.finals[0][2] == "failed"
    assert recorder.finals[0][3] == "provider_error"


async def test_abandon_cancels_inflight_work() -> None:
    recorder = Recorder()
    recorder.gate = asyncio.Event()
    coordinator = _coordinator(recorder)

    coordinator.observe("a" * 300)
    await _settle()
    coordinator.abandon()

    assert coordinator.terminal is True
    assert coordinator.inflight is False
