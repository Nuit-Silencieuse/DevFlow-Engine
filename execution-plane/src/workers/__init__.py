from .activities import registered_activities

__all__ = ["TASK_QUEUE", "create_worker", "registered_activities"]


def __getattr__(name):
    if name in {"TASK_QUEUE", "create_worker"}:
        from . import worker

        return getattr(worker, name)
    raise AttributeError(name)
