import time
from makeup_translator.storage import Storage
from makeup_translator.tasks import TaskManager
from tests.task_worker_helpers import clarification_worker, slow_worker
from tests.test_tasks import wait_for_terminal


def test_wait_releases_slot_survives_restart_and_resumes_same_id(tmp_path):
    storage = Storage(tmp_path/'db.sqlite3')
    manager = TaskManager(storage)
    task = manager.submit(kind='tutorial', worker=clarification_worker, args=(), timeout_seconds=5)
    assert wait_for_terminal(manager, task.id).status == 'waiting_user'
    other = manager.submit(kind='tutorial',worker=slow_worker,args=(.1,),timeout_seconds=5)
    assert wait_for_terminal(manager,other.id).status == 'succeeded'
    elapsed = manager.get(task.id).elapsed_seconds
    time.sleep(.1)
    assert manager.get(task.id).elapsed_seconds == elapsed
    manager.shutdown()
    restored = TaskManager(storage)
    assert restored.get(task.id).status == 'waiting_user'
    resumed = restored.resume(task.id,worker=slow_worker,args=(.01,),on_success=None)
    assert resumed.id == task.id
    assert wait_for_terminal(restored,task.id).status == 'succeeded'
    restored.shutdown()


def test_wait_cancel_survives_restart(tmp_path):
    storage = Storage(tmp_path/'db.sqlite3')
    manager = TaskManager(storage)
    task = manager.submit(kind='tutorial',worker=clarification_worker,args=(),timeout_seconds=5)
    wait_for_terminal(manager,task.id)
    manager.shutdown()
    restored = TaskManager(storage)
    assert restored.cancel(task.id).status == 'cancelled'
    assert TaskManager(storage).get(task.id).status == 'cancelled'
