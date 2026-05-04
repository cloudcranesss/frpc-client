# FRP Web Client（Python Async）

基于 `FastAPI + asyncio + SQLite` 的单用户 FRP 面板，面向 Docker 常驻运维场景。

## 核心能力

- 多客户端管理：新增、切换、删除、配置编辑
- 会话鉴权：登录、登出、账号资料修改（用户名/密码）
- `frpc` 进程管理：启动/停止、异常退出自动重启（指数退避）
- 实时可观测：SSE 推送 `status/log/event/heartbeat`
- 告警能力：Webhook 渠道与规则管理
- 运维维护：导出/导入、快照回滚、只读维护模式、诊断包下载
- 模板库：模板增删改查、变量提取（`${VAR}`）
- 审计日志：关键操作留痕

## 页面入口

- `/`：控制台（客户端配置、启动停止、预检、日志、账号设置）
- `/events`：运行事件流与筛选
- `/alerts`：告警渠道与规则
- `/maintenance`：备份恢复、快照、模板、审计
- `/login`：登录页

## 关键 API

- 鉴权与账号
  - `POST /api/auth/login`
  - `POST /api/auth/logout`
  - `GET /api/auth/status`
  - `GET /api/auth/profile`
  - `PUT /api/auth/profile`
  - `POST /api/auth/change-password`（兼容保留）
- 客户端
  - `GET /api/clients`
  - `POST /api/clients/{client_id}/preflight`
  - `POST /api/clients/{client_id}/start?force=true|false`
  - `POST /api/clients/{client_id}/stop`
  - `GET /api/clients/{client_id}/stream`
  - `GET /api/clients/{client_id}/events`
- 告警
  - `GET/POST /api/alerts/channels`
  - `PUT/DELETE /api/alerts/channels/{id}`
  - `GET/PUT /api/alerts/rules`
- 运维维护
  - `POST /api/maintenance/export`
  - `POST /api/maintenance/import/preview`
  - `POST /api/maintenance/import/apply`
  - `GET /api/maintenance/snapshots`
  - `POST /api/maintenance/snapshots/rollback`
  - `PUT /api/maintenance/read-only`
  - `GET /api/maintenance/state`
  - `GET /api/maintenance/diagnostics`
- 模板与审计
  - `GET/POST /api/templates`
  - `PUT/DELETE /api/templates/{template_id}`
  - `GET /api/audit/logs`

## 账号与密码策略

- 用户名：3-32 位，只允许字母、数字、`_`、`-`
- 密码：至少 8 位，且必须同时包含字母和数字
- 修改账号资料后会强制会话失效，需要重新登录
- 登录页仅在浏览器本地保存“上次用户名”，不保存密码

初始化账号优先级（仅数据库为空时生效）：

1. 环境变量 `FRP_PANEL_INIT_USERNAME` + `FRP_PANEL_INIT_PASSWORD`
2. 旧版 `data/auth.json` 迁移
3. 默认 `admin / admin123456`

## 健康检查

- `GET /health/live`：进程存活
- `GET /health/ready`：存储可用性（SQLite 可读写、数据目录可写、磁盘余量阈值）

相关环境变量：

- `FRP_PANEL_MIN_FREE_MB`：`/health/ready` 的最小可用空间阈值（默认 `50`）
- `FRP_PANEL_CORS_ORIGINS`：显式跨域白名单（默认同源）
- `FRP_PANEL_SECURE_COOKIE`：是否启用 `Secure` Cookie（默认 `false`）

## 本地运行

```powershell
cd F:\Code\frp_web_client
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

`run.py` 会在启动前自动检查并安装缺失依赖（基于 `requirements.txt`）。
如需关闭自动安装，可设置环境变量 `FRP_PANEL_AUTO_INSTALL_DEPS=false`。

## Docker 运行

```bash
docker build -t frp-web-client:latest .
docker run -d \
  --name frp-web-client \
  -p 8000:8000 \
  -v /opt/frp_web_client_data:/app/data \
  --restart unless-stopped \
  frp-web-client:latest
```

## 镜像标签规则

- 推送到 `main`：生成 `latest`
- 推送发布标签 `v1.2.3`：生成 `1.2.3`
- 不再生成 `main`、`v1.2.3`、`sha-*` 等标签
- 历史镜像标签不回填，仅对后续构建生效

## 数据目录

`data/` 由 SQLite 托管核心数据，默认文件：

- `app.db`：主数据库
- `configs/*.toml`：客户端配置落地副本
- `*.bak`：历史 JSON/TOML 迁移备份文件

## 开发测试

```powershell
cd F:\Code\frp_web_client
pip install -r requirements-dev.txt
python -m pytest -q
```
