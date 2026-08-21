# Grok Register

基于 FastAPI、React 和 Camoufox 的 Web 注册管理工具。支持注册任务、账号管理，以及 CPA / Grok2API 授权文件生成。

[部署文档](DEPLOYMENT.md) · [Web 说明](WEB.md)

## 界面预览

### 工作概览

![Grok Register 工作概览](docs/images/dashboard.png)

### 注册、监控与账号

| 新建注册 | 运行监控 | 账号列表 |
| --- | --- | --- |
| ![新建注册页面](docs/images/register.png) | ![运行监控页面](docs/images/runtime.png) | ![账号管理页面](docs/images/accounts.png) |

## 功能

- Web 控制台：任务进度、实时日志、账号管理和系统设置
- Camoufox 浏览器，支持多 worker 和异常进程清理
- 支持 Cloudflare、DuckMail / Mail.tm、YYDS、MailNest、OutlookEmail、CloudMail、Inbucket（自建）
- 注册完成后生成 CPA / Grok2API JSON
- JSON 查看、复制和下载
- 首次访问创建唯一管理员账号
- Docker Compose 部署，支持无桌面 Linux 服务器
- GitHub Actions 自动构建 GHCR 镜像

## Docker 快速启动

宿主机只需安装 Docker 和 Docker Compose。

```bash
git clone https://github.com/kaibush/grok-register.git
cd grok-register
cp .env.example .env
docker compose build
docker compose up -d
```

访问：`http://服务器IP:8787`

> 外部端口由 `compose.yaml` 的端口映射控制（如 `10006:8787`），容器内部固定 `8787`；改外部端口直接编辑该映射后 `docker compose up -d` 重建即可。

查看状态和日志：

```bash
docker compose ps
docker compose logs -f grok-register
curl http://127.0.0.1:8787/api/health
```

容器内使用 **Xvfb + 有头 Camoufox**，服务器不需要桌面环境。Docker 模式会强制关闭无头模式。

如果配置里的代理是 `127.0.0.1:7897`，Compose 会自动映射到宿主机代理。宿主机代理软件需要允许局域网连接（监听 `0.0.0.0` 或 Docker 网桥地址）。

完整说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 可选 OutlookEmail 邮箱池

Compose 已集成 [`ghcr.io/assast/outlookemail:latest`](https://github.com/assast/outlookEmail)，默认不随主服务启动。需要选择 OutlookEmail 邮箱、导入账号或读取邮件时，在 `.env` 修改登录密码和 `SECRET_KEY`，然后启动可选 profile：

```bash
docker compose --profile outlookemail up -d
```

访问地址：

```text
Grok Register: http://服务器IP:8787
OutlookEmail:  http://服务器IP:5000
```

`5000` 默认映射到宿主机所有网卡。主容器内的 API Base 使用：

```text
http://outlook-email:5000
```

Docker 首次生成 `data/config.json` 时会预填该内部地址；已有配置可在“系统设置 → Outlook 邮箱池”中填写。

OutlookEmail 数据保存在 `outlookemail-data/`，并已被 Git 和 Docker 构建上下文忽略。完整配置见 [DEPLOYMENT.md](DEPLOYMENT.md#可选-outlookemail-邮箱池)。

## 配置文件

### 本机运行

读取根目录：

```text
config.json
```

首次使用：

```bash
cp config.example.json config.json
```

### Docker 运行

读取宿主机：

```text
data/config.json
```

使用已有的本地配置：

```bash
mkdir -p data
cp config.json data/config.json
docker compose restart grok-register
```

也可以在 Web 的“系统设置”中修改配置。

Docker 配置中的授权目录建议保持：

```json
{
  "cpa_auth_dir": "data/cpa_auth",
  "grok2api_auth_dir": "data/grok2api_auth",
  "grok2api_remote_url": "https://grok2api.example.com",
  "grok2api_remote_username": "admin",
  "grok2api_remote_password": "change-me",
  "grok2api_auto_import": true
}
```

## 本机运行

要求：Python 3.10+、Node.js 22+。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m camoufox fetch

cd front
npm install
npm run build
cd ..

cp config.example.json config.json
./start-web.sh
```

访问：`http://127.0.0.1:8787`

Windows 启动：

```powershell
.venv\Scripts\python.exe -m backend.web.cli --host 127.0.0.1 --port 8787
```

## 主要配置

建议直接在 Web 设置页填写。

| 配置项 | 说明 |
| --- | --- |
| `email_provider` | 邮箱服务商 |
| `inbucket_base_url` | Inbucket 站点根地址（自建邮箱） |
| `inbucket_api_key` | Inbucket API Key（可选，配了 API 密钥才填） |
| `inbucket_domain` | Inbucket 收信域名，支持 `*` 通配符随机子域，如 `*.mail.example.com` |
| `register_count` | 注册数量 |
| `register_workers` | 并发数量，默认 1 |
| `proxy` | 注册和 OAuth 请求使用的代理。支持 `http://` 与带认证的 `socks5://user:pass@ip:port`；socks5 由本地 HTTP 代理桥转发，仅注册走代理，邮箱验证码始终直连 |
| `browser_headless` | 本机无头模式；Docker 中强制关闭 |
| `cpa_auto_add` | 注册后生成 CPA 授权 |
| `cpa_auth_dir` | CPA JSON 保存目录 |
| `cpa_remote_url` | CPA Management API 地址 |
| `cpa_management_key` | CPA 管理密钥 |
| `grok2api_auth_dir` | Grok2API JSON 保存目录 |
| `grok2api_remote_url` | 远程 Grok2API 站点根地址 |
| `grok2api_remote_username` | 远程 Grok2API 管理员账号 |
| `grok2api_remote_password` | 远程 Grok2API 管理员密码 |
| `grok2api_auto_import` | JSON 生成后自动登录并导入远程 Grok2API |

配置模板见 [`config.example.json`](config.example.json)。

## 数据目录

```text
data/
├── config.json                   # Docker 配置
├── web_auth.json                 # Web 管理员认证
├── accounts/                     # 账号和注册结果
├── cpa_auth/                     # CPA JSON
└── grok2api_auth/                # Grok2API JSON

logs/                             # 运行日志
outlookemail-data/                # 可选 OutlookEmail 数据
```

`data/`、`logs/` 和本地 `config.json` 已被 Git 忽略。

## 常用命令

```bash
# 停止服务
docker compose down

# 更新本地构建
git pull
docker compose up -d --build

# 验证有头 Camoufox
docker compose run --rm grok-register python /app/docker/camoufox_smoke.py

# 后端测试
.venv/bin/python -m unittest discover -s backend/tests -v

# 前端构建
cd front && npm run build
```

## 常见问题

### Docker 修改配置后未生效

Docker 读取 `data/config.json`，不是根目录 `config.json`。修改后执行：

```bash
docker compose restart grok-register
```

### Camoufox 未安装

```bash
.venv/bin/python -m camoufox fetch
.venv/bin/python -m camoufox version
```

### 公网 HTTPS 登录状态异常

在 `.env` 中设置：

```dotenv
GROK_WEB_COOKIE_SECURE=1
```

然后重建容器：

```bash
docker compose up -d --force-recreate
```

### CPA 没有出现新账号

检查 `cpa_auto_add`、`cpa_auth_dir`，或远程配置 `cpa_remote_url`、`cpa_management_key`，并查看日志中的 `[CPA]` 信息。

## 项目结构

```text
front/                  React 前端
backend/                Python 后端
  web/                  FastAPI、认证与任务调度
  registration/         注册编排、仓储和结果产物
  automation/           Camoufox 浏览器运行时
  integrations/         代理、连通性和授权交换
  mailbox/              邮箱渠道适配
  shared/               公共路径等基础设施
backend/tests/          后端测试
docker/                 容器启动与浏览器验证
docs/images/            Web 界面截图
.github/workflows/      GitHub Actions
data/                   运行数据
  screenshots/          浏览器注册失败现场截图
logs/                   运行日志
outlookemail-data/      可选 OutlookEmail 数据
compose.yaml            Docker Compose 配置
```

## 友情链接

- [Linux.do 社区](https://linux.do)

## License

[MIT](LICENSE)
