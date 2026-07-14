import pytest

from pipeline.data_manager import DataManager
from type_define.graph import Task


def test_process_decompose_rejects_task_without_parent():
    info = {
        "task": {
            "description": "Collect wood",
            "parent_task_list": [],
            "status": Task.success,
        }
    }

    with pytest.raises(IndexError):
        DataManager._process_decompose(info)


def test_process_decompose_uses_first_parent_when_multiple_are_present():
    info = {
        "task": {
            "description": "Collect wood",
            "parent_task_list": ["Build shelter", "Survive night"],
            "status": Task.success,
        }
    }

    assert DataManager._process_decompose(info) == {
        "sub_task": "Collect wood",
        "task": "Build shelter",
        "status": Task.success,
    }
