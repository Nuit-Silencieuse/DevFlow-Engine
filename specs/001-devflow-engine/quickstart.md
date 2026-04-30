# 快速开始

本文档记录当前阶段可执行的启动、测试和验证命令。项目采用“先写测试，再补实现”的 TDD 范式；新增接口或行为时，应先增加失败的契约测试，再实现代码并让测试转绿。

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
  "stages": ["REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION"]
}'
```

查询流水线:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8080/api/v1/pipelines/{pipelineId}"
```

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

## 数据库验证

```powershell
wsl -e docker exec docker-postgres-1 psql -U postgres -d devflow -c "select table_schema, table_name from information_schema.tables where table_schema = 'devflow_app' order by table_name;"
```

预期包含:

- `pipelines`
- `stages`
- `checkpoint_feedback`
- `flyway_schema_history`
