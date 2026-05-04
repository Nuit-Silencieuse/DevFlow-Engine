# 工具契约: 代码上下文渐进探索

## 工具设计原则

- 工具只执行确定性文件系统动作，不自行做需求判断。
- 所有路径都以 `rootPath` 为安全边界。
- 工具返回结构化结果，供 Agent 决定下一步。
- 工具结果默认不返回大段源码；需要读取源码时必须走范围读取和预算控制。

## list_repository

获取仓库目录轮廓和候选文件。

### 输入

```json
{
  "rootPath": "D:/repo",
  "includePaths": [],
  "excludePaths": [],
  "maxFiles": 2000
}
```

### 输出

```json
{
  "files": [
    {
      "path": "execution-plane/src/agents/requirement_agent.py",
      "sizeBytes": 21199,
      "language": "python",
      "priorityHint": "agent"
    }
  ],
  "skipped": [
    {
      "path": ".git",
      "reason": "EXCLUDED",
      "detail": "默认排除 Git 元数据"
    }
  ]
}
```

## search_text

在允许范围内执行关键词搜索。

### 输入

```json
{
  "rootPath": "D:/repo",
  "query": "RequirementAgent",
  "includeGlobs": ["**/*.py", "**/*.java", "**/*.ts"],
  "maxResults": 30
}
```

### 输出

```json
{
  "matches": [
    {
      "path": "execution-plane/src/agents/requirement_agent.py",
      "line": 42,
      "preview": "class RequirementAgent:",
      "scoreHint": 0.95
    }
  ],
  "truncated": false
}
```

## read_file_range

读取文件片段。

### 输入

```json
{
  "rootPath": "D:/repo",
  "path": "execution-plane/src/agents/requirement_agent.py",
  "lineStart": 1,
  "lineEnd": 160,
  "maxBytes": 12000
}
```

### 输出

```json
{
  "path": "execution-plane/src/agents/requirement_agent.py",
  "lineStart": 1,
  "lineEnd": 160,
  "content": "短范围源码内容",
  "bytesRead": 9600,
  "truncated": false
}
```

## summarize_file

对已读取文件或片段生成结构化摘要。该工具可以由 LLM 节点实现，但输入必须来自已审计的读取结果。

### 输出

```json
{
  "path": "execution-plane/src/agents/requirement_agent.py",
  "symbols": ["RequirementAgent.analyze"],
  "responsibilities": ["需求分析", "LLM 调用", "代码上下文注入"],
  "relevanceReason": "这是需求分析 Agent 的核心实现点"
}
```

## evaluate_sufficiency

评估当前证据是否足够支撑需求分析。

### 输出

```json
{
  "confidence": 0.84,
  "isSufficient": true,
  "missingSignals": [],
  "nextQueries": []
}
```

## 默认排除规则

默认排除以下路径或文件类型：

- `.git/`
- `node_modules/`
- `target/`
- `build/`
- `dist/`
- `.venv/`
- `venv/`
- `__pycache__/`
- `.pytest_cache/`
- `.mypy_cache/`
- `.devflow-test-env/`
- `logs/`
- `*.pyc`
- `*.class`
- `*.jar`
- `*.log`
- `.env`
- `.env.*`
- `*secret*`
- `*key*`

## 错误约定

| 错误码 | 含义 |
|--------|------|
| `ROOT_NOT_FOUND` | `rootPath` 不存在。 |
| `PATH_OUT_OF_SCOPE` | 请求路径逃逸仓库根目录。 |
| `FILE_TOO_LARGE` | 文件超过单文件读取上限。 |
| `BINARY_FILE` | 文件被识别为二进制。 |
| `BUDGET_EXHAUSTED` | 探索预算耗尽。 |
| `READ_DENIED` | 文件无法读取或权限不足。 |
