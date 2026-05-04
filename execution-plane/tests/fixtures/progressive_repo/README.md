# Progressive Exploration Fixture Repository

这个目录是渐进式代码库探索 Agent 的测试夹具，模拟一个小型服务仓库。

## 模块

- `src/health_service.py`: 健康检查业务模块，包含控制平面、执行平面和 Temporal worker 状态聚合的关键词。
- `src/temporal_worker.py`: 执行平面 worker 状态模块，包含 worker polling、task queue 和最近心跳时间等信号。
- `node_modules/ignored.js`: 模拟依赖目录，探索工具默认应跳过。
- `logs/runtime.log`: 模拟运行日志，探索工具默认应跳过。

测试应验证 Agent 在只给出仓库根目录时能发现 `src/` 下的相关文件，同时不会把依赖目录和日志文件作为候选上下文读取。
