"""Bounded, process-local scheduling for metadata reads.

The service owns no node state. Future callbacks run on the completing thread;
callers must dispatch updates to their host loop and check their own generation.
Async callers should shield wrapped futures so cancelling one waiter does not
cancel a pending read shared with another consumer.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from contextvars import Context, copy_context
from dataclasses import dataclass
from threading import Condition, Lock, Thread, current_thread
from typing import cast


class MetadataSuperseded(Exception):
    """A newer selection replaced a pending metadata read."""


class MetadataServiceClosed(Exception):
    """The service shut down or the owner retired a pending request."""


@dataclass(frozen=True)
class _Request[T]:
    owner: object
    selection: object
    read: Callable[[], T]
    context: Context
    future: Future[T]


logger = logging.getLogger("griptape_nodes")


def _settle[T](future: Future[T], *, value: T | None = None, error: BaseException | None = None) -> None:
    """Isolate completion callbacks so they cannot strand the reader queue."""
    try:
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(cast("T", value))
    except BaseException:
        logger.exception("Metadata completion callback failed")


def _retire(request: _Request[object], error: Exception) -> None:
    """Settle a removed pending request without racing external cancellation."""
    if request.future.set_running_or_notify_cancel():
        _settle(request.future, error=error)


class MetadataService:
    """Run one read at a time, retaining at most one pending selection per owner.

    Owners are matched by identity, including unhashable owners. Selections must
    be stable snapshots with scalar equality. Replacing a pending request keeps
    its owner's place in the FIFO queue. No completed results are cached.
    """

    def __init__(self) -> None:
        self._condition = Condition(Lock())
        self._pending: OrderedDict[int, _Request[object]] = OrderedDict()
        self._running: _Request[object] | None = None
        self._thread: Thread | None = None
        self._closed = False

    def submit[T](self, owner: object, selection: object, read: Callable[[], T]) -> Future[T]:
        """Enqueue a read or join this owner's matching active selection.

        A closed service returns a future failed with MetadataServiceClosed.
        Submitting never runs disk I/O inline. The first read lazily starts one
        daemon thread, which is not joined by Python's executor exit machinery.
        """
        retired: _Request[object] | None = None
        rejected: Exception | None = None
        context = copy_context()
        with self._condition:
            key = id(owner)
            pending = self._pending.get(key)
            running = self._running
            if self._closed:
                future: Future[T] = Future()
                rejected = MetadataServiceClosed("Metadata service is closed")
            elif (
                running is not None
                and running.owner is owner
                and running.selection == selection
                and not running.future.done()
            ):
                future = cast("Future[T]", running.future)
                # A/B/A rejoins A, but B must not run after the latest selection.
                retired = self._pending.pop(key, None)
            elif pending is not None and pending.selection == selection and not pending.future.done():
                future = cast("Future[T]", pending.future)
            else:
                future = Future()
                request = _Request(owner, selection, read, context, future)
                retired = pending
                self._pending[key] = cast("_Request[object]", request)
                if self._thread is None:
                    thread = Thread(target=self._run, name="OpenEXR metadata reader", daemon=True)
                    # Start while holding the condition so no submitter can see
                    # an assigned thread that has not actually started yet.
                    try:
                        thread.start()
                    except RuntimeError as error:
                        self._pending.pop(key)
                        rejected = error
                    else:
                        self._thread = thread
                self._condition.notify()

        # Future completion invokes arbitrary callbacks synchronously. Never do
        # this under either the scheduler lock or the singleton lock.
        if retired is not None:
            _retire(retired, MetadataSuperseded("A newer metadata selection superseded this request"))
        if rejected is not None:
            _settle(future, error=rejected)
        return future

    @property
    def closed(self) -> bool:
        """Whether the service has stopped accepting requests and node updates."""
        with self._condition:
            return self._closed

    def close_owner(self, owner: object) -> None:
        """Retire this owner's pending read; a running read finishes normally.

        This does not retain the owner or permanently ban later submissions.
        The caller must invalidate its node generation to reject late updates.
        """
        with self._condition:
            pending = self._pending.pop(id(owner), None)
        if pending is not None:
            _retire(pending, MetadataServiceClosed("Metadata owner is closed"))

    def shutdown(self, wait: bool = False) -> None:
        """Stop submissions and retire pending work, letting a running read finish.

        The default never joins a potentially blocked native read. Explicit
        waiting can block indefinitely; waiting from the reader's own callback
        cannot join itself and is therefore skipped.
        """
        with self._condition:
            self._closed = True
            pending = list(self._pending.values())
            self._pending.clear()
            thread = self._thread
            self._condition.notify_all()
        for request in pending:
            _retire(request, MetadataServiceClosed("Metadata service is closed"))
        if wait and thread is not None and thread is not current_thread():
            thread.join()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or bool(self._pending))
                if self._closed:
                    return
                _, request = self._pending.popitem(last=False)
                self._running = request
            if not request.future.set_running_or_notify_cancel():
                with self._condition:
                    self._running = None
                continue
            try:
                value = request.context.run(request.read)
            except BaseException as error:
                # A callable's process-control exception must still settle its
                # future and not strand the remaining queue on a dead worker.
                _settle(request.future, error=error)
            else:
                _settle(request.future, value=value)
            finally:
                with self._condition:
                    self._running = None


_service_lock = Lock()
_service: MetadataService | None = None


def get_metadata_service() -> MetadataService:
    """Return the lazy singleton for this hosting process."""
    global _service
    with _service_lock:
        if _service is None:
            _service = MetadataService()
        return _service


def shutdown_metadata_service(wait: bool = False) -> None:
    """Detach and shut down the singleton; a later lookup creates a new service."""
    global _service
    with _service_lock:
        service = _service
        _service = None
    if service is not None:
        service.shutdown(wait=wait)
