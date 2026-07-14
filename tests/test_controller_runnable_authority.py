from pipeline.controller_tiny import GlobalController
from type_define.graph import Task


def test_controller_final_available_check_does_not_recompute_store_authority():
    controller = object.__new__(GlobalController)
    runnable = Task("Runnable", {})
    runnable.available = True
    runnable.status = Task.unknown
    blocked = Task("Blocked", {})
    blocked.available = False
    blocked.status = Task.unknown
    running = Task("Running", {})
    running.available = True
    running.status = Task.running
    controller.task_list = [runnable, blocked, running]

    assert controller.check_task_list_available() == [runnable]
