# FRP Web Client (Python Async)

一个基于 `FastAPI + asyncio` 的 FRP 网页客户端，提供：

- 多客户端可视化管理（新增 / 切换 / 删除）
- 登录鉴权（会话 Cookie）
- `frpc` 路径配置
- `frpc.toml` 在线编辑
- 每个客户端独立启动 / 停止 `frpc`
- 查看运行状态与实时日志（轮询）

## 1. 安装

```powershell
cd F:\Code\frp_web_client
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. 准备 frpc

推荐自动补齐（同时下载 Windows + Linux amd64）：

```powershell
cd F:\Code\frp_web_client
python scripts\fetch_frpc.py
```

下载后默认路径：

- Windows: `bin/windows/frpc.exe`
- Linux: `bin/linux/frpc`
- Web UI 支持“自动选择路径”按钮，会按当前系统优先选择可用文件

你也可以任选其一：

- 在网页中填写你自己的绝对路径，例如 `D:\tools\frp\frpc.exe`
- 或仅填写 `frpc`（要求系统 `PATH` 可找到）

## 3. 启动服务

推荐在项目根目录直接运行：

```powershell
cd F:\Code\frp_web_client
python run.py
```

或使用 uvicorn：

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

或在 `app` 目录直接运行：

```powershell
cd F:\Code\frp_web_client\app
python main.py
```

打开浏览器访问：

- [http://127.0.0.1:8000](http://127.0.0.1:8000)
- 默认登录账号：`admin`
- 默认登录密码：`admin123456`

首次登录后建议立刻在页面右上角“改密码”。

## 4. Docker 部署

本项目已提供 `Dockerfile`，镜像内会自动下载 Linux `frpc`。

本地构建：

```bash
cd /opt/frp_web_client
docker build -t frp-web-client:latest .
```

本地运行（持久化 `data/`）：

```bash
docker run -d \
  --name frp-web-client \
  -p 8000:8000 \
  -v /opt/frp_web_client_data:/app/data \
  --restart unless-stopped \
  frp-web-client:latest
```

访问：

- [http://127.0.0.1:8000](http://127.0.0.1:8000)

建议在反代 HTTPS 场景下保留默认环境变量：

- `FRP_PANEL_SECURE_COOKIE=true`

## 5. GitHub Actions 自动构建镜像

已内置工作流文件：

- `.github/workflows/docker-image.yml`

默认行为：

- Push 到 `main` 自动构建并推送
- Push `v*` tag 自动构建并推送
- 手动触发（workflow_dispatch）支持

默认推送地址：

- `ghcr.io/<你的仓库名>`

## 6. 默认配置示例

首次启动会自动创建默认客户端，可在页面里继续新增多个客户端配置。

## 7. 数据文件

运行后会在 `data/` 目录生成：

- `client_config.json`：网页保存的配置
- `auth.json`：登录账号与密码哈希
- `frpc.toml`：当前激活客户端配置（兼容文件）
- `configs/<client_id>.toml`：每个客户端独立配置文件
