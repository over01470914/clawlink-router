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
|---|---|---|
| `CLAWLINK_ROUTER_HOST` | `0.0.0.0` | bind host |
| `CLAWLINK_ROUTER_PORT` | `8420` | bind port |

## Core API Routes

All routes are mounted at root (no built-in `/api` prefix).

| Method | Path | Purpose |
|---|---|---|
| POST | `/connect` | validate connection |
| POST | `/pair/generate` | generate pairing code |
| POST | `/pair/validate` | validate pairing code |
| POST | `/pair/complete` | complete pairing and register agent |
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
| GET | `/sessions/{session_id}/heartbeat` | heartbeat status |
| GET | `/health` | health check |
| GET | `/metrics` | aggregated metrics |

## Integration Note For Clients Expecting `/api`

If a client expects `/api/*`, use reverse proxy path rewrite, or configure client base URL accordingly.

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

### 防硬编码清单

- host / port 通过环境变量配置。
- 各客户端部署地址通过配置管理。
- 避免把固定机器 IP 写进代码。