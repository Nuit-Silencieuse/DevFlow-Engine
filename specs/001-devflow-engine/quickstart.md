# 快速开始

本文档记录当前阶段可执行的启动、测试和验证命令。项目采用“先写测试，再补实现”的 TDD 范式；新增接口或行为时，应先增加失败的契约测试，再实现代码并让测试转绿。

控制平面的结构、核心类字段和复杂方法流程见 [control-plane-architecture.md](./control-plane-architecture.md)。
执行平面的结构、LangGraph 实现和 Worker 说明见 [execution-plane-architecture.md](./execution-plane-architecture.md)。
T021-T026 的 Agent 职责、输入输出和验收要求见 [agent-design.md](./agent-design.md)。

## 基础设施

基础设施运行在 WSL Docker 容器中。

```powershell
wsl -e docker compose -f /mnt/d/ZPY/Agent学习/DevFlow-Engine/docker/docker-compose.yml ps
```

预期至少包含:

- `docker-postgres-1`
- `docker-temporal-1`
- `docker-temporal-ui-1`

## 控制平面测试

Java 控制平面位于 `control-plane/devflow-engine`。

```powershell
mvn "-Dmaven.repo.local=C:\Users\12252\.m2\repository" test
```

当前测试覆盖:

- `PipelineControllerContractTest`: 验证流水线 REST API 创建、查询和人工检查点响应。
- `PipelineServiceTest`: 验证流水线持久化、阶段初始化、Temporal Workflow 启动和 Signal 转发。
- `DevFlowWorkflowContractTest`: 验证 Temporal Workflow 与 Activity 接口注解契约。
- `DevFlowWorkflowImplTest`: 验证 Workflow 编排、批准继续执行、驳回后注入反馈并重跑设计阶段。

## 控制平面启动验证

```powershell
mvn "-Dmaven.repo.local=C:\Users\12252\.m2\repository" "-DskipTests" "-Dspring-boot.run.arguments=--spring.main.web-application-type=none" spring-boot:run
```

预期结果:

- Flyway 校验 `devflow_app` schema 已是最新。
- Hibernate 成功初始化实体映射。
- 应用启动后正常退出。

## 控制平面 API 验证

在 WSL Docker 中启动 Postgres 和 Temporal 后，可以启动控制平面 Web 服务:

```powershell
mvn "-Dmaven.repo.local=C:\Users\12252\.m2\repository" spring-boot:run
```

创建流水线:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/v1/pipelines" -ContentType "application/json" -Body '{
  "name": "Add user authentication",
  "requirement": "实现用户登录、注册和鉴权",
  "stages": ["REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION"],
  "repository": {
    "rootPath": "D:/projects/demo-app",
    "includePaths": ["src", "README.md"],
    "excludePaths": ["node_modules", "dist", ".git"],
    "targetFiles": ["src/App.tsx"],
    "maxFiles": 50,
    "maxBytes": 65536
  }
}'
```

查询流水线:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8080/api/v1/pipelines/{pipelineId}"
```

查询响应会回显 `repository` 上下文，后续执行平面 T019 会使用该上下文进行路径驱动的代码感知。

提交人工检查点:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/v1/pipelines/{pipelineId}/checkpoints/SYSTEM_DESIGN" -ContentType "application/json" -Body '{
  "decision": "APPROVE",
  "feedback": ""
}'
```

注意: T016 之前执行平面的 Temporal Worker 尚未实现，因此 Workflow 可以被创建和接收 Signal，但实际 Activity 执行需要后续 Worker 注册后才会完整推进。

## 本地 Daemon 测试

Node Daemon 位于 `sandbox/daemon`。

```powershell
npm.cmd test
npm.cmd run typecheck
```

当前测试覆盖:

- `POST /api/sandbox/modify` 接收合法修改请求并返回 `202 Accepted`。
- 非法请求返回 `400 Bad Request`。

本地启动 Daemon:

```powershell
npm.cmd start
```

默认监听地址:

`http://localhost:8080`

## 执行平面测试

Python 执行平面位于 `execution-plane`。当前本地虚拟环境为 Python 3.10，可直接运行:

```powershell
.\venv\python.exe -m unittest discover -s tests
```

当前测试覆盖:

- `tests/test_flow.py`: 验证 LangGraph 六阶段拓扑和人工反馈注入。
- `tests/test_worker.py`: 验证 Temporal Activity 注册名与 Java 契约一致，并验证 Activity 返回 `StageExecutionResult` 形状。

执行平面依赖记录在:

`execution-plane/requirements.txt`

连接基础设施:

```powershell
.\venv\python.exe test_connection.py
```

启动 Python Temporal Activity Worker:

```powershell
.\venv\python.exe -m src.workers.worker
```

Worker 默认连接 `localhost:7233`，可通过 `TEMPORAL_TARGET` 覆盖；注册的 Task Queue 为 `DEVFLOW_TASK_QUEUE`。

注意: 当前 Python Worker 注册的是 Activity Worker。Java `DevFlowWorkflowImpl` 仍需要控制平面侧 Workflow Worker 承载后，真实 Temporal 流水线才能完整推进。

## 数据库验证

```powershell
wsl -e docker exec docker-postgres-1 psql -U postgres -d devflow -c "select table_schema, table_name from information_schema.tables where table_schema = 'devflow_app' order by table_name;"
```

预期包含:

- `pipelines`
- `stages`
- `checkpoint_feedback`
- `flyway_schema_history`
