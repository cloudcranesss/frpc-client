# FRP Web Client（Python Async）

基于 `FastAPI + asyncio + JSON + SSE` 的单用户 FRP 管理面板，面向 Docker 常驻场景。

## 核心能力

- 客户端管理：新增、切换、删除、配置编辑
- 配置格式：支持 `TOML / INI / JSON` 三种配置文本
- 进程控制：`start/stop`、状态查询、异常自动重启（指数退避）
- 开机自启动：按客户端粒度配置，服务启动后自动拉起
- 实时可观测：SSE 推送 `status/log/event/heartbeat`
- 鉴权会话：登录、登出、账号资料修改（用户名/密码）
- 告警能力：Webhook 渠道与规则管理
- 维护能力：导出、导入预检、导入执行、诊断包下载

## 页面结构（极简双页）

- `/`：控制台（配置、启停、状态、实时日志）
- `/settings`：设置（账号、告警、备份恢复）
- `/login`：登录页

兼容入口说明：

- `/events`、`/alerts`、`/maintenance` 会重定向到 `/settings`

## 已下线能力

以下低频功能已下线（前后端与数据库同步裁剪）：

- 模板库：`/api/templates*`
- 审计日志：`/api/audit/logs`
- 快照管理：`/api/maintenance/snapshots*`
- 维护状态/只读模式：`/api/maintenance/state`、`/api/maintenance/read-only`

## 关键 API

### 鉴权

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/status`
- `GET /api/auth/profile`
- `PUT /api/auth/profile`
- `POST /api/auth/change-password`（兼容保留）

### 客户端

- `GET /api/clients`
- `POST /api/clients/{client_id}/preflight`
- `POST /api/clients/{client_id}/start?force=true|false`
- `POST /api/clients/{client_id}/stop`
- `GET /api/clients/{client_id}/stream`
- `GET /api/clients/{client_id}/events`

### 告警

- `GET/POST /api/alerts/channels`
- `PUT/DELETE /api/alerts/channels/{id}`
- `GET/PUT /api/alerts/rules`

### 维护

- `POST /api/maintenance/export`
- `POST /api/maintenance/import/preview`
- `POST /api/maintenance/import/apply`
- `GET /api/maintenance/diagnostics`

## 账号策略

- 用户名：3-32 位，仅允许字母、数字、`_`、`-`
- 密码：至少 8 位，且必须同时包含字母和数字
- 修改账号资料后会强制会话失效，需要重新登录
- 登录页仅在本地记录上次用户名，不保存密码

首启账号优先级（仅用户存储为空时）：

1. `FRP_PANEL_INIT_USERNAME` + `FRP_PANEL_INIT_PASSWORD`
2. 旧版 `data/auth.json` 迁移
3. 默认 `admin / admin123456`

## 健康检查

- `GET /health/live`：进程存活
- `GET /health/ready`：存储可用（`data/store.json` 可读写、`data/` 可写、磁盘剩余空间满足阈值）

相关环境变量：

- `FRP_PANEL_MIN_FREE_MB`：`/health/ready` 最小可用空间阈值（默认 `50`）
- `FRP_PANEL_CORS_ORIGINS`：跨域白名单（默认同源）
- `FRP_PANEL_SECURE_COOKIE`：是否启用 `Secure` Cookie（默认 `false`）
- `FRP_PANEL_ASSET_VERSION`：前端静态资源版本戳（可选，建议发版时递增，用于强制浏览器更新 CSS/JS/SVG）

## 本地运行

```powershell
cd F:\Code\frp_web_client
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

`run.py` 会在启动前自动检查并安装缺失依赖（基于 `requirements.txt`）。
如需关闭自动安装，设置 `FRP_PANEL_AUTO_INSTALL_DEPS=false`。

## Docker Compose（Host 网络 + 端口可配置）

`docker-compose.yml` 默认使用：

- `network_mode: host`
- `FRP_PANEL_PORT=8000`
- `FRP_VERSION=0.68.0`（可通过 compose build args 覆盖）
- 启动命令 `--port $${FRP_PANEL_PORT:-8000}`

启动：

```bash
docker compose pull
docker compose up -d
```

## 镜像标签规则

- `main` 分支构建：发布 `latest`
- Git 标签 `v1.2.3` 构建：发布 `1.2.3`
- 不再生成 `main`、`v1.2.3`、`sha-*`
- 历史镜像标签不回填，仅对后续构建生效

## 数据目录

`data/` 默认包含：

- `store.json`：主存储文件（客户端、告警、用户、会话、事件）
- `configs/*.toml`：客户端配置落地副本
- `*.bak`：历史 JSON/TOML 迁移备份

## 开发测试

```powershell
cd F:\Code\frp_web_client
pip install -r requirements-dev.txt
python -m pytest -q
```

## IP 地区分组（主页）

- 主页会按 IP 地区分组展示探活成功站点（国家/省份/城市）。
- 地区数据来自在线 `ip.sb`（`https://api.ip.sb/geoip/{ip}`）。
- 环境变量：
  - `FRP_PANEL_GEO_API_BASE`：地区查询 API 基地址（默认 `https://api.ip.sb/geoip`）。
  - `FRP_PANEL_GEO_API_TIMEOUT_SEC`：地区查询超时秒数（默认 `2`）。
  - `FRP_PANEL_GEOIP_CACHE_TTL_SEC`：地区缓存 TTL 秒数（默认 `1800`）。
- 废弃：`FRP_PANEL_GEOIP_DB_PATH`（已不再使用，设置后会在日志中提示）。
- 当外网不可达或接口异常时，会自动降级为 `内网/未知` 分组，不影响探活与页面访问。
