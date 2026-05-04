"""Temporal worker 状态测试夹具。

该文件用于模拟执行平面 worker 的关键状态，帮助搜索工具命中 worker、task queue、
polling、heartbeat 等需求分析常用信号。
"""


DEVFLOW_TASK_QUEUE = "DEVFLOW_TASK_QUEUE"


class TemporalWorkerStatus:
    """描述执行平面 worker 是否正在轮询任务队列。"""

    def __init__(self, task_queue=DEVFLOW_TASK_QUEUE):
        self.task_queue = task_queue
        self.polling = False
        self.last_heartbeat_at = None

    def mark_polling(self, heartbeat_time):
        """记录 worker 已开始 polling，并保存最近一次心跳时间。"""
        self.polling = True
        self.last_heartbeat_at = heartbeat_time

    def to_health_payload(self):
        """转换为健康检查页面可以展示的结构。"""
        return {
            "taskQueue": self.task_queue,
            "workerRunning": self.polling,
            "lastHeartbeatAt": self.last_heartbeat_at,
        }
