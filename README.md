# CLAWLINK-ROUTER

ClawLink central router service for multi-agent orchestration.

Typical target: cloud server Docker deployment.

## Deployment Role

This component is the control plane for the whole system:

1. agent pairing and registration
2. session lifecycle and message routing
3. teaching loop orchestration
4. group topic message flow
5. file lock coordination
6. queue and health/metrics exposure

## Independent Runtime Contract

Router can run independently as long as agents can reach it.

- Default port: `8420`
- Health endpoint: `/health`
- WebSocket endpoint: `/ws/{session_id}`
- No hardcoded downstream agent address is required in code

## Run With Docker

Build image:

```bash
cd clawlink-router
docker build -t clawlink-router:local .
```

Start container:

```bash
docker run -d \
  --name clawlink-router \
  -p 8420:8420 \
  -e CLAWLINK_ROUTER_HOST=0.0.0.0 \
  -e CLAWLINK_ROUTER_PORT=8420 \
  clawlink-router:local
```

Verify:

```bash
curl http://localhost:8420/health
```

## Run From Source

```bash
cd clawlink-router
pip install -e .
python run.py
```

## Configuration

| Environment Variable | Default | Description |
| --- | --- | --- |
| `CLAWLINK_ROUTER_HOST` | `0.0.0.0` | bind host |
| `CLAWLINK_ROUTER_PORT` | `8420` | bind port |

## Core API Routes

All routes are mounted at root (no built-in `/api` prefix).

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/connect` | validate connection |
| POST | `/pair/generate` | generate pairing code |
| POST | `/pair/validate` | validate pairing code |
| POST | `/pair/complete` | complete pairing and register agent |
| POST | `/agents/register` | direct agent registration compatibility endpoint |
| GET | `/agents` | list agents |
| GET | `/agents/{agent_id}` | get agent |
| DELETE | `/agents/{agent_id}` | disconnect agent |
| POST | `/sessions` | create session |
| GET | `/sessions` | list sessions |
| GET | `/sessions/{session_id}` | get session |
| POST | `/sessions/{session_id}/message` | send message |
| POST | `/sessions/{session_id}/teach` | start teaching loop |
| PUT | `/sessions/{session_id}/strictness` | update strictness |
| DELETE | `/sessions/{session_id}` | delete session |
| GET | `/sessions/{session_id}/queue` | view queue |
| POST | `/sessions/{session_id}/queue/flush` | flush queue |
| POST | `/sessions/{session_id}/topics` | create topic |
| GET | `/sessions/{session_id}/topics` | list topics |
| GET | `/sessions/{session_id}/topics/{topic_id}/messages` | topic messages |
| POST | `/sessions/{session_id}/fetch-messages` | agent pull messages |
| POST | `/locks/acquire` | acquire lock |
| POST | `/locks/release` | release lock |
| GET | `/locks` | list locks |
| GET | `/locks/{file_path}` | check lock |
| DELETE | `/locks/{file_path}` | force release lock |
| GET | `/status` | compatibility status summary |
| GET | `/api/status` | legacy status alias |
| GET | `/sessions/{session_id}/heartbeat` | heartbeat status |
| GET | `/health` | health check |
| GET | `/metrics` | aggregated metrics |

## Integration Note For Clients Expecting `/api`

If a client expects `/api/*`, use reverse proxy path rewrite, or configure client base URL accordingly.

For local integration with `clawlink-agent`, the Router also accepts direct self-registration on `/agents/register`.

## Extracted Essentials From Global Docs

### Teaching Session Flow

1. create session with mode, strictness, participants
2. send prompt to learner agent
3. receive response and score it
4. if score is below threshold, iterate correction loop
5. on pass or max iterations, finalize and persist learning outcome in agent side memory

### Group Chat Flow

1. create group topic in a session
2. send message with optional mentions
3. route to target agents and broadcast updates
4. agents pull message history when needed

### Scoring Formula Reference

Router-level evaluation convention:

```text
overall = (accuracy * 0.4) + (completeness * 0.3) + (clarity * 0.3)
```

At higher strictness levels, passing becomes harder through strictness curve adjustment.

## Anti-Hardcoding Checklist

- Keep host and port configurable with env vars.
- Keep deployment endpoint configurable in every client.
- Avoid embedding fixed machine IPs in code.

## License

MIT

---

## 中文说明

ClawLink Router 是多 Agent 协作系统的中心路由服务，典型部署目标是云服务器 Docker。

### 组件职责

1. Agent 配对与注册
2. Session 生命周期与消息路由
3. 教学循环编排
4. 群组主题消息流转
5. 文件锁协调
6. 队列、健康检查与指标输出

### 独立运行契约

Router 可独立运行，只要 Agent 能访问它。

- 默认端口：`8420`
- 健康接口：`/health`
- WebSocket：`/ws/{session_id}`
- 代码中不需要写死下游 Agent 地址

### Docker 运行

构建镜像：

```bash
cd clawlink-router
docker build -t clawlink-router:local .
```

启动容器：

```bash
docker run -d \
  --name clawlink-router \
  -p 8420:8420 \
  -e CLAWLINK_ROUTER_HOST=0.0.0.0 \
  -e CLAWLINK_ROUTER_PORT=8420 \
  clawlink-router:local
```

验证：

```bash
curl http://localhost:8420/health
```

### 本地源码运行

```bash
cd clawlink-router
pip install -e .
python run.py
```

### 路由前缀说明

当前 Router 路由挂载在根路径（没有内建 `/api` 前缀）。

如果客户端预期 `/api/*`，请使用反向代理重写（如 `/api/* -> /*`），或将客户端 base URL 指向兼容网关。

为了兼容旧配置，Router 现在也提供：

- `/status`
- `/api/status`
- `/agents/register`

### 从全局 docs 提炼的关键机制

#### 教学会话流程

1. 创建会话（mode、strictness、participants）
2. 发送教学提示给学习者 Agent
3. 接收回复并评分
4. 低于阈值则进入纠正迭代
5. 达标或达到最大迭代后结束，并由 Agent 侧持久化学习结果

#### 群聊流程

1. 在会话中创建群组主题
2. 发送消息，可带 mentions
3. Router 路由到目标 Agent 并广播更新
4. Agent 需要时可主动拉取历史消息

#### 评分公式参考

```text
overall = (accuracy * 0.4) + (completeness * 0.3) + (clarity * 0.3)
```

strictness 越高，通过难度越高。

### 防硬编码清单

- host / port 通过环境变量配置。
- 各客户端部署地址通过配置管理。
- 避免把固定机器 IP 写进代码。
