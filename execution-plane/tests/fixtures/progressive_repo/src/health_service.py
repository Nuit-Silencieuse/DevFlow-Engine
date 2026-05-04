"""健康检查测试夹具。

这个文件刻意包含 health check、control plane、execution plane、Temporal worker
等关键词，供渐进探索工具在没有 include/exclude 的情况下通过搜索定位。
"""


class HealthService:
    """聚合测试环境各个组件的健康状态。"""

    def __init__(self, control_plane_client, worker_registry):
        self.control_plane_client = control_plane_client
        self.worker_registry = worker_registry

    def collect_health_snapshot(self):
        """返回控制平面、执行平面和 Temporal worker 的状态摘要。"""
        return {
            "controlPlane": self.control_plane_client.ping(),
            "executionPlane": self.worker_registry.execution_plane_status(),
            "temporalWorker": self.worker_registry.temporal_worker_status(),
        }

    def is_ready_for_pipeline_test(self):
        """只有控制平面和 worker 都可用时，测试流水线才应允许启动。"""
        snapshot = self.collect_health_snapshot()
        return (
            snapshot["controlPlane"] == "UP"
            and snapshot["executionPlane"] == "UP"
            and snapshot["temporalWorker"] == "POLLING"
        )
