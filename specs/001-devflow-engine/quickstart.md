# 快速入门与本地环境启动指南

DevFlow Engine 包含多个子模块，以下为本地全栈环境的快速拉起指南。

## 前置环境依赖
- Docker & Docker Compose (用于启动 Temporal, PostgreSQL, Milvus)
- JDK 17 及 Maven
- Python 3.10+ 及 Poetry
- Node.js 18+ 及 npm/pnpm

## 步骤 1: 启动基础中间件
在项目根目录 `docker/` 下：
```bash
cd docker
docker-compose up -d
```
*这将会启动 PostgreSQL、Temporal Server、Temporal Web UI 以及 Milvus 向量引擎。*

## 步骤 2: 启动执行平面 (Python Worker)
执行平面的 Worker 负责连接大模型并执行 LangGraph 的逻辑。
```bash
cd execution-plane
poetry install
# 配置模型 API Key
export OPENAI_API_KEY="sk-..."
export TEMPORAL_HOST="localhost:7233"
# 启动 worker
poetry run python -m workers.main
```

## 步骤 3: 启动控制平面 (Java Spring Boot)
控制平面负责暴露 REST API 并管理工作流。
```bash
cd control-plane
mvn clean install
# 启动 Spring Boot 应用
mvn spring-boot:run
```

## 步骤 4: 启动前端沙箱与守护进程
本地守护进程将监听目标页面的修改请求：
```bash
cd sandbox/daemon
npm install
npm run start
```

目标被修改的网页应用（如使用 Vite 启动），需确保证明加入了 Babel 映射插件：
```bash
# 在待测试前端项目中
npm run dev
```

通过浏览器访问前端面板应用（由 control-plane 或独立服务提供）以创建和查看 Pipeline 进度。访问 Temporal Web UI (`http://localhost:8233`) 可以观察每个工作流的具体执行历史和故障状态。