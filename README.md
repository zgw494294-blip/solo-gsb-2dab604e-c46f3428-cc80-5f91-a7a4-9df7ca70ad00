# 舞台提示单 · StageCue

面向小型演出（话剧、音乐会、校园演出等）的**舞台提示单 Web 应用**。
在一条可缩放的时间轴上编排 **灯光 / 音效 / 换景** 三类提示（Cue），
自动按依赖关系实时计算每个提示的执行时间；拖动固定提示或修改时长后，
所有后继提示联动重排。

技术栈：**Flask 3 + SQLite + 原生 JavaScript（无构建、无前端框架）+ SVG 时间轴**。

---

## 一键启动（Docker）

前置条件：已安装 Docker 与 Docker Compose v2（随 Docker Desktop / Docker Engine 自带）。

```bash
docker compose up -d --build
```

首次启动会自动：

1. 安装 Python 运行依赖（Flask、gunicorn）；
2. 初始化 SQLite 数据库（建表），并写入一份可直接体验的**示例提示单**；
3. 以 gunicorn 启动 Web 服务。

启动后访问：

> **http://localhost:8000**

停止与查看日志：

```bash
docker compose down          # 停止（数据保留在命名卷中）
docker compose logs -f       # 跟踪日志
```

> 使用旧版独立 `docker-compose` 的环境，把上面的 `docker compose` 换成
> `docker-compose` 即可（仓库根目录的 `compose.yaml` 两种方式都识别）。

---

## 环境变量与配置方法

均为**可选**变量，不配也能直接跑（默认值见下表）。

| 变量 | 默认值 | 说明 | 配置方法 |
| --- | --- | --- | --- |
| `STAGECUE_PORT` | `8000` | **宿主机**暴露端口。容器内固定监听 8000。 | 在仓库根目录创建 `.env` 文件写入，如 `STAGECUE_PORT=9000`，然后 `docker compose up -d`；访问地址相应变为 `http://localhost:9000` |
| `DATABASE_PATH` | `/data/stagecue.db` | 容器内 SQLite 文件路径 | 一般无需修改；改后需自行保证所在目录已持久化挂载（`compose.yaml` 默认把命名卷挂到 `/data`） |
| `GUNICORN_WORKERS` | `2` | gunicorn worker 进程数 | 编辑 `compose.yaml` 的 `environment` 段 |
| `GUNICORN_TIMEOUT` | `60` | 请求超时秒数 | 同上 |
| `TZ` | `Asia/Shanghai` | 容器时区 | 同上 |

数据持久化：`compose.yaml` 声明了命名卷 `stagecue-data` 挂载到 `/data`，
删除并重建容器不会丢数据；`docker compose down -v` 才会连同数据卷一起删除。

### 备份与迁移

- 备份：`docker run --rm -v stagecue_stagecue-data:/data -v "$PWD":/backup alpine cp /data/stagecue.db /backup/`
  （卷名前缀以 `docker volume ls` 实际输出为准），或直接使用应用内置的
  **导出 JSON** 功能逐场备份。
- 恢复：把 db 文件拷回卷内，或用 **导入 JSON** 新建一场演出。

---

## 功能说明

### 提示与时间模型

每个提示（Cue）有两类开始时间，互斥：

- **固定时间（fixed）**：相对开场的固定秒数，可在时间轴上**横向拖动**改变；
- **在另一提示结束后（after）**：开始时间 = 被依赖提示的结束时间 + 延迟秒数，
  其中 结束时间 = 开始时间 + 时长。

系统在浏览器内**实时**重算全部时间；保存时由后端用同一套规则再次校验。

### 时间轴

- 三类提示分三条泳道（灯光=蓝、音效=绿、换景=紫）；
- **拖动固定提示色块** → 修改固定开始时间，后继链整体平移；
- **拖动色块右缘手柄** → 修改时长，后继链联动重排；
- 「在…之后」的提示不能直接拖时间（它的时间由依赖决定），点击可编辑；
- 虚线箭头表示依赖关系；
- **滚轮缩放**（以鼠标位置为锚点）、`＋/−` 按钮、「适应」一键铺满；
- 按住空白处拖动可平移；
- 无法落地的提示（依赖链上没有任何固定提示）显示为虚线块并归入底部「异常」行。

### 循环依赖防护（重点）

- 新增 / 编辑时，编辑器底部会**实时预览**计算结果；一旦当前修改会成环，
  立即红字提示；
- 点击保存若后端检测到环，返回 **HTTP 409** 与**完整冲突链**，弹窗中按
  `A → B → C → A` 的形式展示每一条环（首尾同名，直观表示回到自身）；
- **保存被整体拒绝，数据库不变；弹窗保持打开、表单里的编辑内容原样保留**，
  用户改完可再次保存；
- 导入 JSON 同样做环检测，含环文件整单拒绝并指出冲突链；
- 算法可同时找出图中**多个相互独立**的环（三色 DFS）。

### 演出管理与导入导出

- 顶栏可新建、重命名、删除、切换多张提示单（按演出保存）；
- **导出 JSON**：`GET /api/shows/<id>/export`（浏览器按钮直接下载）；
- **导入 JSON**：顶栏「导入 JSON」选择文件，或 `POST /api/import`。
  导入会新建一场演出，不覆盖现有数据。

#### JSON 文件格式（`stagecue/v1`）

```json
{
  "format": "stagecue/v1",
  "exported_at": "2026-09-17T00:00:00+00:00",
  "show": { "name": "演出名称", "description": "说明" },
  "cues": [
    {
      "position": 0,
      "cue_type": "lighting",          // lighting | sound | scene
      "name": "第一束面光",
      "note": "打在船长位",
      "mode": "fixed",                  // fixed | after
      "fixed_at": 12.0,                 // fixed 模式：开始秒数
      "depends_on_index": null,         // after 模式：被依赖提示在 cues 数组中的下标
      "delay": 0,                       // after 模式：结束后延迟秒数
      "duration": 3.5
    }
  ]
}
```

依赖用 **数组下标** `depends_on_index` 表达（从 0 开始），与数据库 id 无关，
因此文件可以在任意实例间移植。时间字段均为秒，支持小数。

---

## HTTP API 速览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/shows` | 演出列表（含提示数量） |
| POST | `/api/shows` | 新建演出 `{name, description?}` |
| GET | `/api/shows/<id>` | 演出详情：`show` + `cues` + `schedule`（实时计算结果） |
| PUT | `/api/shows/<id>` | 重命名 / 改说明 |
| DELETE | `/api/shows/<id>` | 删除演出及其全部提示（级联） |
| POST | `/api/shows/<id>/cues` | 新增提示 |
| PUT | `/api/cues/<id>` | 修改提示（含拖动 / 改时长） |
| DELETE | `/api/cues/<id>` | 删除提示（仍被依赖时返回 409 并列出依赖者） |
| GET | `/api/shows/<id>/export` | 导出 JSON（带下载文件名） |
| POST | `/api/import` | 导入 JSON，创建新演出 |

提示写入字段示例：

```json
{
  "position": 0,
  "cue_type": "sound",
  "name": "海浪音效",
  "note": "音量 -18dB",
  "mode": "after",
  "depends_on": 12,
  "delay": 1.5,
  "duration": 120
}
```

错误统一为 JSON：`{"error": "...", ...}`；循环依赖冲突为 `409`，
额外携带 `cycles`（id 链数组）与 `conflict_chains`（带名称的可读链）。

---

## 本地开发（不用 Docker）

需要 Python 3.11+：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_PATH=./data/stagecue.db     # 可选，默认 /data/stagecue.db
flask --app wsgi.py init-db                 # 建库建表（加 --no-seed 跳过示例）
python wsgi.py                              # 开发服务器，http://localhost:8000
# 或：gunicorn --bind 0.0.0.0:8000 wsgi:app
```

### 测试

```bash
python -m unittest discover -s tests       # 调度算法单元测试
PYTHONPATH=. python tests/smoke_api.py      # API 端到端冒烟（临时库）
```

---

## 目录结构

```
.
├── Dockerfile              # 运行镜像（python:3.12-slim + gunicorn）
├── compose.yaml            # 一键编排：构建、端口、环境变量、数据卷
├── entrypoint.sh           # 容器启动：init-db 初始化 → 启动 gunicorn
├── requirements.txt
├── wsgi.py                 # 应用入口
├── app/
│   ├── __init__.py         # Flask 应用工厂与配置
│   ├── db.py               # SQLite 连接、建表、示例数据
│   ├── scheduler.py        # 时间编排 / 多环检测核心（纯函数，可单测）
│   ├── routes.py           # 页面与 JSON API、导入导出、校验
│   └── static/             # index.html / styles.css / app.js（原生 JS）
└── tests/
    ├── test_scheduler.py
    └── smoke_api.py
```

后续迭代只要继续通过 `docker compose up -d --build` 启动即可；
数据库表结构变更请在 `app/db.py` 的 schema 中以可重入方式（`IF NOT EXISTS`）演进，
保证老数据卷上的容器重启不失败。
