# DevFlow-Engine 开发指南

基于功能计划自动生成并校正。最后更新时间: 2026-05-04

## 活跃技术

- Python 3.10+、Java 17+、TypeScript
- LangGraph、Temporal Python SDK、项目内 LLM Client
- Spring Boot、Temporal Java SDK、JPA、H2/PostgreSQL
- Vite/Vitest

## 项目结构

```text
control-plane/devflow-engine/   # Java 控制平面、REST API、Temporal workflow/worker
execution-plane/                # Python 执行平面、LangGraph Agent、上下文工具、LLM 客户端
sandbox/frontend/               # Vite 前端测试控制台
specs/                          # Spec Kit 规格、计划和任务制品
scripts/                        # 本地/测试环境脚本
```

## 常用命令

```powershell
.\venv\python.exe -m unittest discover -s tests
mvn "-Dmaven.repo.local=C:\Users\12252\.m2\repository" test
cd sandbox\frontend
npm.cmd test
npm.cmd run build
```

## 代码风格

- Python 执行平面优先使用结构化 dataclass/dict 输出，测试以 `unittest` 为主。
- Java 控制平面遵循 Spring Boot + JPA + Temporal SDK 既有分层。
- TypeScript 前端保持轻量、可测试的 API client 和 view model。
- 涉及 Agent、工具调用、状态流转和中间产物的代码，应保留清晰中文注释。

## 最近变更

- `002-progressive-code-exploration`: 规划将 T019 路径驱动工具升级为工具调用式渐进探索 Agent，默认只要求仓库根目录，include/exclude 作为高级选项。

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
