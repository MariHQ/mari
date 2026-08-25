"""Within-step progress for flow runs.

A three-step flow whose last step is a multi-minute model pass reported
0 -> 33 -> 66 and then parked at 66% until the end: step-count granularity
says nothing while the only long step runs. The runner arms a reporter
around each step; a step that loops over real units of work calls
``report(done, total)`` and the bar moves through that step's own share.

A contextvar rather than a ctx entry because ctx is persisted as JSON with
every heartbeat, and a callable cannot ride in it. The reporter is armed on
the runner's thread and read on the same thread (the scan pools report from
their collection loop, not from the workers), so thread-locality holds.
"""

from __future__ import annotations

import contextvars
import typing as t

_REPORTER: contextvars.ContextVar[t.Callable[[int, int], None] | None] = contextvars.ContextVar(
    "mari_step_progress", default=None)


def arm(reporter: t.Callable[[int, int], None]) -> contextvars.Token:
    return _REPORTER.set(reporter)


def disarm(token: contextvars.Token) -> None:
    _REPORTER.reset(token)


def report(done: int, total: int) -> None:
    """Report progress inside the current step. No-op outside a run, and a
    reporter that raises must not fail the work it is narrating."""
    reporter = _REPORTER.get()
    if reporter is None:
        return
    try:
        reporter(done, total)
    except Exception:  # noqa: BLE001 — narration must never break the scan
        pass
