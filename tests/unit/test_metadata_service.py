"""Controlled reader tests; no engine, OpenEXR or runner dependencies."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event, Thread, current_thread
from unittest.mock import patch

import pytest

from griptape_nodes_openexr.exr.metadata_service import (
    MetadataService,
    MetadataServiceClosed,
    MetadataSuperseded,
    get_metadata_service,
    shutdown_metadata_service,
)

TIMEOUT = 3


class BlockedRead:
    def __init__(self, value: str = "first") -> None:
        self.value = value
        self.started = Event()
        self.release = Event()
        self.thread: Thread | None = None

    def __call__(self) -> str:
        self.thread = current_thread()
        self.started.set()
        assert self.release.wait(TIMEOUT), "Test did not release reader"
        return self.value


@pytest.fixture
def service() -> Iterator[MetadataService]:
    reader = MetadataService()
    yield reader
    reader.shutdown(wait=True)


def result[T](future: Future[T]) -> T:
    return future.result(timeout=TIMEOUT)


def test_completion_callback_process_exception_does_not_strand_queue(service: MetadataService) -> None:
    from threading import Event

    started, release = Event(), Event()

    def read() -> int:
        started.set()
        assert release.wait(5)
        return 1

    first = service.submit(object(), "first", read)
    assert started.wait(5)

    def callback(_future) -> None:
        raise SystemExit("callback failure")

    first.add_done_callback(callback)
    next_read = service.submit(object(), "next", lambda: 2)
    release.set()
    assert next_read.result(timeout=5) == 2


def test_thread_is_lazy_and_started_once(service: MetadataService) -> None:
    with patch("griptape_nodes_openexr.exr.metadata_service.Thread", wraps=Thread) as thread:
        unused = MetadataService()
        unused.shutdown(wait=True)
        thread.assert_not_called()
        assert result(service.submit(object(), "first", lambda: "first")) == "first"
        assert result(service.submit(object(), "second", lambda: "second")) == "second"
        assert thread.call_count == 1
        assert thread.call_args.kwargs["daemon"] is True


def test_thread_start_failure_settles_request_and_allows_retry(service: MetadataService) -> None:
    with patch("griptape_nodes_openexr.exr.metadata_service.Thread.start", side_effect=RuntimeError("start failed")):
        failed = service.submit(object(), "failed", lambda: "never")
    with pytest.raises(RuntimeError, match="start failed"):
        result(failed)
    assert result(service.submit(object(), "retry", lambda: "retry")) == "retry"


def test_matching_running_request_reuses_future(service: MetadataService) -> None:
    owner = object()
    read = BlockedRead()
    first = service.submit(owner, ("shot", 1), read)
    try:
        assert read.started.wait(TIMEOUT)
        assert service.submit(owner, ("shot", 1), lambda: "duplicate") is first
    finally:
        read.release.set()
    assert result(first) == "first"


def test_completed_selection_is_read_again(service: MetadataService) -> None:
    owner = object()
    assert result(service.submit(owner, "same", lambda: "first")) == "first"
    assert result(service.submit(owner, "same", lambda: "fresh")) == "fresh"


def test_latest_pending_replaces_old_and_matching_pending_reuses(service: MetadataService) -> None:
    owner = object()
    read = BlockedRead()
    first = service.submit(owner, "A", read)
    try:
        assert read.started.wait(TIMEOUT)
        old = service.submit(owner, "B", lambda: "obsolete")
        latest = service.submit(owner, "C", lambda: "latest")
        assert service.submit(owner, "C", lambda: "duplicate") is latest
        with pytest.raises(MetadataSuperseded):
            result(old)
        assert not first.done()
        assert not latest.done()
    finally:
        read.release.set()
    assert result(first) == "first"
    assert result(latest) == "latest"


def test_return_to_running_selection_retires_obsolete_pending(service: MetadataService) -> None:
    owner = object()
    read = BlockedRead()
    first = service.submit(owner, "A", read)
    try:
        assert read.started.wait(TIMEOUT)
        old = service.submit(owner, "B", lambda: "obsolete")
        assert service.submit(owner, "A", lambda: "duplicate") is first
        with pytest.raises(MetadataSuperseded):
            result(old)
    finally:
        read.release.set()
    assert result(first) == "first"


def test_fairness_preserves_pending_owner_position(service: MetadataService) -> None:
    owner_a, owner_b, owner_c = object(), object(), object()
    order: list[str] = []

    def record(value: str) -> str:
        order.append(value)
        return value

    read = BlockedRead()
    running = service.submit(owner_a, "A0", read)
    try:
        assert read.started.wait(TIMEOUT)
        old_b = service.submit(owner_b, "B0", lambda: record("obsolete"))
        pending_c = service.submit(owner_c, "C", lambda: record("C"))
        pending_a = service.submit(owner_a, "A1", lambda: record("A"))
        pending_b = service.submit(owner_b, "B1", lambda: record("B"))
        with pytest.raises(MetadataSuperseded):
            result(old_b)
    finally:
        read.release.set()
    result(running)
    assert [result(future) for future in (pending_b, pending_c, pending_a)] == ["B", "C", "A"]
    assert order == ["B", "C", "A"]


def test_owner_identity_not_equality_and_unhashable_selections(service: MetadataService) -> None:
    owner_a: object = []
    owner_b: object = []
    read = BlockedRead()
    running = service.submit(owner_a, ["same"], read)
    try:
        assert read.started.wait(TIMEOUT)
        other = service.submit(owner_b, ["same"], lambda: "other owner")
        assert other is not running
        assert service.submit(owner_b, ["same"], lambda: "duplicate") is other
    finally:
        read.release.set()
    assert result(running) == "first"
    assert result(other) == "other owner"


def test_concurrent_submission_coalesces_and_uses_one_daemon_reader(service: MetadataService) -> None:
    owner = object()
    read = BlockedRead()
    try:
        with ThreadPoolExecutor(max_workers=16) as submitters:
            futures = list(submitters.map(lambda _: service.submit(owner, "same", read), range(64)))
        assert read.started.wait(TIMEOUT)
        assert all(future is futures[0] for future in futures)
        assert read.thread is not None
        assert read.thread.daemon
        assert read.thread is not current_thread()
    finally:
        read.release.set()
    assert result(futures[0]) == "first"
    assert result(service.submit(object(), "next", current_thread)) is read.thread


def test_concurrent_owners_share_one_serial_reader(service: MetadataService) -> None:
    read = BlockedRead()
    running = service.submit(object(), "block", read)
    threads: list[Thread] = []

    def record() -> int:
        threads.append(current_thread())
        return len(threads)

    try:
        assert read.started.wait(TIMEOUT)
        with ThreadPoolExecutor(max_workers=16) as submitters:
            pending = list(submitters.map(lambda index: service.submit(object(), index, record), range(64)))
        assert threads == []
        assert all(not future.done() for future in pending)
    finally:
        read.release.set()
    assert result(running) == "first"
    assert sorted(result(future) for future in pending) == list(range(1, 65))
    assert all(thread is read.thread for thread in threads)


def test_cancelled_pending_request_does_not_kill_reader(service: MetadataService) -> None:
    read = BlockedRead()
    running = service.submit(object(), "block", read)
    try:
        assert read.started.wait(TIMEOUT)
        cancelled = service.submit(object(), "cancel", lambda: pytest.fail("Cancelled request ran"))
        assert cancelled.cancel()
        next_request = service.submit(object(), "next", lambda: "next")
    finally:
        read.release.set()
    assert result(running) == "first"
    assert result(next_request) == "next"
    assert cancelled.cancelled()


def test_context_is_captured_at_submission_including_pending(service: MetadataService) -> None:
    context = ContextVar("metadata-context", default="missing")
    read = BlockedRead()
    running = service.submit(object(), "block", read)
    try:
        assert read.started.wait(TIMEOUT)
        context.set("captured")
        pending = service.submit(object(), "pending", context.get)
        context.set("changed")
    finally:
        read.release.set()
    result(running)
    assert result(pending) == "captured"
    assert result(service.submit(object(), "next", context.get)) == "changed"


def test_read_failure_settles_future_and_worker_continues(service: MetadataService) -> None:
    error = ValueError("bad metadata")

    def fail() -> str:
        raise error

    with pytest.raises(ValueError, match="bad metadata") as caught:
        result(service.submit(object(), "bad", fail))
    assert caught.value is error
    assert result(service.submit(object(), "good", lambda: "good")) == "good"


@pytest.mark.parametrize("operation", ["replace", "close_owner", "shutdown"])
def test_retirement_callbacks_can_reenter_service(service: MetadataService, operation: str) -> None:
    read = BlockedRead()
    owner = object()
    running = service.submit(object(), "block", read)
    finished = Event()
    callbacks: list[Future[str]] = []
    try:
        assert read.started.wait(TIMEOUT)
        old = service.submit(owner, "old", lambda: "old")
        old.add_done_callback(lambda _: callbacks.append(service.submit(object(), "callback", lambda: "callback")))

        def retire() -> None:
            if operation == "replace":
                service.submit(owner, "new", lambda: "new")
            elif operation == "close_owner":
                service.close_owner(owner)
            else:
                service.shutdown()
            finished.set()

        thread = Thread(target=retire, daemon=True)
        thread.start()
        assert finished.wait(TIMEOUT), "Future callback ran under service lock"
        thread.join(TIMEOUT)
        assert old.done()
        assert len(callbacks) == 1
    finally:
        read.release.set()
    result(running)
    if operation == "shutdown":
        with pytest.raises(MetadataServiceClosed):
            result(callbacks[0])
    else:
        assert result(callbacks[0]) == "callback"


def test_completion_callback_can_submit_and_shutdown_on_reader_thread(service: MetadataService) -> None:
    read = BlockedRead()
    running = service.submit(object(), "block", read)
    callback_done = Event()
    submitted: list[Future[str]] = []
    try:
        assert read.started.wait(TIMEOUT)

        def callback(_: Future[str]) -> None:
            submitted.append(service.submit(object(), "callback", lambda: "callback"))
            service.shutdown(wait=True)
            callback_done.set()

        running.add_done_callback(callback)
    finally:
        read.release.set()
    assert result(running) == "first"
    assert callback_done.wait(TIMEOUT)
    with pytest.raises(MetadataServiceClosed):
        result(submitted[0])


def test_shutdown_does_not_wait_for_running_read_and_settles_every_pending(service: MetadataService) -> None:
    read = BlockedRead()
    owner = object()
    running = service.submit(owner, "block", read)
    try:
        assert read.started.wait(TIMEOUT)
        pending = [service.submit(object(), index, lambda: "never") for index in range(5)]
        shutdown_done = Event()
        thread = Thread(target=lambda: (service.shutdown(wait=False), shutdown_done.set()), daemon=True)
        thread.start()
        assert shutdown_done.wait(TIMEOUT), "Shutdown waited for blocked reader"
        thread.join(TIMEOUT)
        assert not running.done()
        for future in pending:
            with pytest.raises(MetadataServiceClosed):
                result(future)
        with pytest.raises(MetadataServiceClosed):
            result(service.submit(owner, "block", lambda: "rejected even if matching"))
        service.shutdown(wait=False)
    finally:
        read.release.set()
    assert result(running) == "first"


def test_close_owner_retires_only_its_pending_request(service: MetadataService) -> None:
    owner = object()
    read = BlockedRead()
    running = service.submit(owner, "block", read)
    try:
        assert read.started.wait(TIMEOUT)
        removed = service.submit(owner, "removed", lambda: "removed")
        other = service.submit(object(), "other", lambda: "other")
        service.close_owner(owner)
        service.close_owner(owner)
        with pytest.raises(MetadataServiceClosed):
            result(removed)
        assert not running.done()
        assert not other.done()
    finally:
        read.release.set()
    assert result(running) == "first"
    assert result(other) == "other"


def test_singleton_shutdown_callbacks_can_recreate_service() -> None:
    shutdown_metadata_service()
    service = get_metadata_service()
    read = BlockedRead()
    running = service.submit(object(), "block", read)
    recreated: list[MetadataService] = []
    shutdown_done = Event()
    try:
        assert read.started.wait(TIMEOUT)
        pending = service.submit(object(), "pending", lambda: "never")
        pending.add_done_callback(lambda _: recreated.append(get_metadata_service()))

        def shutdown() -> None:
            shutdown_metadata_service()
            shutdown_done.set()

        thread = Thread(target=shutdown, daemon=True)
        thread.start()
        assert shutdown_done.wait(TIMEOUT), "Callback ran under singleton lock"
        thread.join(TIMEOUT)
        assert len(recreated) == 1
        assert recreated[0] is not service
        assert recreated[0] is get_metadata_service()
    finally:
        read.release.set()
        service.shutdown(wait=True)
        shutdown_metadata_service(wait=True)
    assert result(running) == "first"


def test_global_service_is_lazy_shared_and_recreated_after_shutdown() -> None:
    shutdown_metadata_service()
    try:
        with ThreadPoolExecutor(max_workers=8) as callers:
            services = list(callers.map(lambda _: get_metadata_service(), range(32)))
        assert all(service is services[0] for service in services)
        shutdown_metadata_service()
        with pytest.raises(MetadataServiceClosed):
            result(services[0].submit(object(), "closed", lambda: "closed"))
        replacement = get_metadata_service()
        assert replacement is not services[0]
        assert result(replacement.submit(object(), "new", lambda: "new")) == "new"
    finally:
        shutdown_metadata_service(wait=True)


def test_cancelling_shielded_asyncio_waiter_preserves_shared_future(service: MetadataService) -> None:
    async def exercise() -> None:
        read = BlockedRead()
        future = service.submit(object(), "shared", read)
        try:
            assert read.started.wait(TIMEOUT)

            async def wait() -> str:
                return await asyncio.shield(asyncio.wrap_future(future))

            cancelled = asyncio.create_task(wait())
            surviving = asyncio.create_task(wait())
            await asyncio.sleep(0)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert not future.cancelled()
            read.release.set()
            assert await asyncio.wait_for(surviving, TIMEOUT) == "first"
        finally:
            read.release.set()

    asyncio.run(exercise())
