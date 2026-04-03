# BetterPPT

BetterPPT 是一个模板驱动的 PDF -> PPT 自动生成系统。项目当前处于 **v0.1.0（首个可交付版本）**，已经具备本地运行、回归测试与基础协作开发能力。

## 项目解决的问题

在实际汇报材料生产中，常见问题是：
- 有 PDF 内容，但难以快速整理成可编辑 PPT
- 有历史模板，但套版和结构重建耗时
- 多人协作下，任务过程与质量缺少可追踪性

BetterPPT 的目标是把这条链路工程化：上传源 PDF + 参考模板，生成可下载结果，并提供回放、映射与质量报告接口。

## 技术栈

- Backend: Python 3.11, FastAPI, SQLAlchemy
- Queue/Cache: Redis
- Database: MySQL（开发态也可用本地 DB 进行部分流程）
- PPT 处理: python-pptx
- PDF 处理: pypdf
- Frontend: 原生 HTML/CSS/JS（调试与联调用）
- Test: unittest / pytest

## 主要功能

- 文件上传与下载（分片外链上传位点）
- 任务创建、查询、取消、重试、删除
- 任务回放（steps/events）
- 模板资产化与资产查询
- 任务映射查询（attempt_no / cursor / limit）
- 质量报告查询
- 预览与结果下载
- 指标总览（metrics overview）

## 快速开始

### 1) 前置条件

- Python 3.11+
- MySQL 8+
- Redis 6+
- Windows PowerShell（当前脚本默认以 PowerShell 为主）

### 2) 安装依赖

```powershell
cd source/backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

可选：启用本地真实 Vision embedding（模板多模态分析）：

```powershell
cd <repo-root>
source\backend\.venv\Scripts\python.exe bin\setup_local_vision_model.py
```

说明：该脚本会安装 CPU 版 `torch` 并将 ViT 模型下载到 `source/backend/models/vision/`，运行时按离线模式加载。

### 3) 准备环境变量

```powershell
cd <repo-root>
Copy-Item .env.example source\backend\.env
```

编辑 `source/backend/.env`，填入实际配置（特别是 `LLM_API_KEY`、MySQL、Redis）。

### 4) 启动后端 API

```powershell
cd source/backend
.\.venv\Scripts\python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5) 启动 Worker（新终端）

```powershell
cd source/backend
.\.venv\Scripts\python -m app.workers.runner
```

### 6) 启动前端联调页（新终端）

```powershell
cd source/frontend
python -m http.server 5173
```

打开 `http://127.0.0.1:5173`。

## 环境变量说明

核心变量见 [`.env.example`](./.env.example)。重点如下：

| 变量 | 用途 | 是否必填 |
| --- | --- | --- |
| `APP_ENV` | 运行环境标识 | 否 |
| `APP_PORT` | API 端口 | 否 |
| `MYSQL_*` / `DATABASE_URL` | 数据库连接 | 是 |
| `REDIS_*` | 队列与状态存储 | 是 |
| `LLM_API_BASE` | 模型服务地址 | 是 |
| `LLM_API_KEY` | 模型服务密钥 | 是 |
| `LLM_MODEL` | 模型名 | 是 |
| `LLM_REQUEST_TIMEOUT_SECONDS` | 单次 LLM 请求超时（秒） | 否 |
| `LLM_REQUEST_MAX_RETRIES` | LLM 请求最大重试次数 | 否 |
| `UPLOAD_PDF_MAX_FILE_SIZE_MB` | PDF 上传大小上限（MB） | 否 |
| `UPLOAD_REFERENCE_PPT_MAX_FILE_SIZE_MB` | 参考 PPT 上传大小上限（MB） | 否 |
| `TASK_CONCURRENCY_PER_USER` | 单用户并发任务阈值 | 否 |
| `TASK_CONCURRENCY_ACTIVE_WINDOW_MINUTES` | 并发活跃窗口（分钟，过滤历史僵尸任务） | 否 |
| `BETTERPPT_TEMPLATE_VISION_MODEL_PATH` | 本地 Vision 模型目录（离线优先） | 否 |
| `BETTERPPT_TEMPLATE_VISION_CACHE_DIR` | 本地 HF 缓存目录 | 否 |
| `LOCAL_STORAGE_ROOT` | 本地存储根目录 | 否 |

## 测试说明

### 单元测试

```powershell
source\backend\.venv\Scripts\python.exe -m pytest source\backend\tests\unit -q
```

### 全量回归

```powershell
.\bin\full_regression_round.ps1
```

### 发布前检查

```powershell
.\bin\pre_release_precheck.ps1
```

更多细节见 [docs/release_preflight_checklist.md](./docs/release_preflight_checklist.md)。

## 部署说明

部署建议与环境拓扑见 [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md)。

说明：生产部署细节（如高可用、容灾、监控告警）目前为 **待补充**。

## 目录结构

```text
.
├─bin/                     # 回归与发布前检查脚本
├─docs/                    # 需求、技术设计、发布文档
├─ref/                     # 本地测试样例（默认不建议公开提交）
├─source/
│  ├─backend/              # FastAPI + worker + tests + migrations
│  └─frontend/             # 最小联调页面
├─.env.example
├─.gitignore
└─README.md
```

## 版本说明

- 首版发布说明见 [docs/RELEASE_NOTES_v0.1.0.md](./docs/RELEASE_NOTES_v0.1.0.md)
- 文档版本变更见 [docs/CHANGELOG.md](./docs/CHANGELOG.md)

## 已知问题

- 当前鉴权默认支持开发态匿名用户（`user_id=1`），生产环境需要改为真实鉴权体系。
- 前端为调试联调页，尚未建设完整产品化 UI。
- 部分回归脚本依赖 `ref/` 下本地样例文件；公开仓库建议提供可再分发的示例素材。

## 后续计划

- [ ] 完整生产级鉴权与权限模型（待补充）
- [ ] CI 中增加集成测试矩阵（待补充）
- [ ] Docker 一键启动全链路（待补充）
- [ ] 公共示例数据集与数据脱敏策略（待补充）

## 贡献指南

协作规范见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## License

默认采用 MIT License，见 [LICENSE](./LICENSE)。

