from __future__ import annotations

import time

from makeup_translator.tasks import TaskManager
from tests.task_worker_helpers import slow_worker


def wait_for_terminal(manager: TaskManager, task_id: str, seconds: float = 3):
    deadline = time.monotonic() + seconds
    snapshot = manager.get(task_id)
    while snapshot.status in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.03)
        snapshot = manager.get(task_id)
    return snapshot


def test_timeout_terminates_worker_process():
    manager = TaskManager()
    task = manager.submit(
        kind="tutorial",
        worker=slow_worker,
        args=(5.0,),
        timeout_seconds=0.2,
        foreground_wait_seconds=0.05,
    )
    snapshot = wait_for_terminal(manager, task.id)
    assert snapshot.status == "timed_out"
    assert snapshot.cancellable is False
    assert snapshot.elapsed_seconds < 1
    manager.shutdown()


def test_user_cancel_terminates_worker_process():
    manager = TaskManager()
    task = manager.submit(
        kind="tutorial",
        worker=slow_worker,
        args=(5.0,),
        timeout_seconds=10,
        foreground_wait_seconds=0.05,
    )
    time.sleep(0.08)
    running = manager.get(task.id)
    assert running.status == "running"
    assert running.continued_in_background is True
    snapshot = manager.cancel(task.id)
    assert snapshot.status == "cancelled"
    assert snapshot.cancellable is False
    manager.shutdown()
