from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

ProgressReporter = Callable[[dict[str, Any]], None]

_reporter: ContextVar[ProgressReporter | None] = ContextVar(
    "scrape_gateway_progress_reporter", default=None
)


@contextmanager
def observe_progress(reporter: ProgressReporter) -> Iterator[None]:
    """Send progress emitted in this async context to ``reporter``."""

    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)


def emit_progress(**event: Any) -> None:
    """Emit a best-effort, structured progress update for the current scrape."""

    reporter = _reporter.get()
    if reporter is None:
        return
    try:
        reporter(event)
    except Exception:
        # Operator visibility must never change the scrape result.
        return
