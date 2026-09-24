# 舞台提示单 · Stage Cue

面向小型演出（剧场、Livehouse、校园晚会、彩排）的**舞台提示单（Cue Sheet）** Web 应用。
用 Flask + SQLite + 原生 JavaScript 实现，零前端构建步骤，Docker 一键启动。

## 功能

- **提示管理**：新增 / 编辑 / 删除三类提示——🎡 灯光（light）、🔊 音效（sound）、🔧 换景（scene），含编号、标题、备注、持续时长。
- **两种触发方式**
  - **固定时间**：演出开始后的绝对秒数；
  - **依赖触发**：在「另一提示结束」之后延迟若干秒开始。
- **实时联动排程**：系统按依赖关系（DAG）实时计算每条提示的开始/结束时间。
- **可缩放时间轴**
  - 鼠标 `Ctrl/⌘ + 滚轮`缩放、普通滚轮横向滚动，也可用右上角按钮；
  - 三类提示分泳道、同类型重叠自动分到多条轨道；
  - **横向拖动固定提示**即可改时间，**拖动条体右边缘**改时长；松手保存后，所有后继提示自动联动重排（拖动过程中时间轴实时预览）。
- **循环依赖防护**
  - 编辑弹窗内实时检测，预览"预计开始/结束"或即将形成的循环链；
  - 保存时服务端再次校验，发现循环返回 **HTTP 409** 并给出**完整冲突链**（如 `S1 → L1 → L2 → S1`）；
  - 保存失败时**弹窗保留、用户已输入内容不丢失**，数据库维持原状（写入前校验 + 事务回滚）。
- **多场演出**：按演出分别保存提示单，可随时切换、重命名、删除。
- **JSON 导入 / 导出**：支持导出全部演出或当前演出；导入时整体校验（含循环检测），任一演出有问题则全部回滚，不会写入半截数据。

## 一条命令启动

需要 Docker（含 Compose 插件，Docker Desktop 或 Docker Engine 20.10+ 自带）：

```bash
docker compose up -d --build
```

启动后浏览器访问：

> **http://localhost:8000**

健康检查地址：http://localhost:8000/healthz

首次启动会自动建表并写入一份演示演出《夜航》（可用 `STAGECUE_SEED=0` 关闭）。
停止服务：`docker compose down`（数据保留在命名卷 `stagecue-data` 中；加 `-v` 可同时删除数据）。

## 环境变量与配置

所有配置均通过环境变量完成，可在 `compose.yaml` 中修改，或在项目根目录放 `.env` 文件（例如 `STAGECUE_PORT=8080`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `STAGECUE_PORT` | `8000` | **宿主机映射端口**。想改访问端口只需改它，例如 `STAGECUE_PORT=8080 docker compose up -d --build` |
| `STAGECUE_DB` | `/data/stagecue.db` | 容器内 SQLite 数据库文件路径，默认落在持久化卷上 |
| `STAGECUE_SEED` | `1` | 数据库首次初始化时是否写入演示演出：`1` 写入，`0` 空白启动 |
| `PORT` | `8000` | 容器内 gunicorn 监听端口（一般无需修改） |
| `GUNICORN_WORKERS` | `1` | gunicorn worker 数。SQLite 单文件请保持 `1` |
| `GUNICORN_THREADS` | `4` | 每 worker 线程数 |
| `GUNICORN_TIMEOUT` | `60` | 请求超时秒数 |

数据持久化：compose 已声明命名卷 `stagecue-data` 挂载到 `/data`；
如需改用宿主机目录，把 `compose.yaml` 中的

```yaml
volumes:
  - stagecue-data:/data
```

改成例如 `./data:/data` 即可。

## 不用 Docker 的本地运行（可选）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.server            # 开发模式，监听 0.0.0.0:8000
# 或
gunicorn --bind 0.0.0.0:8000 "app.server:create_app()"
```

## HTTP API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/shows` | 演出列表 |
| `POST` | `/api/shows` | 新建演出 `{name, description?}` |
| `GET` | `/api/shows/{id}` | 演出详情，含全部 cues 与计算出的 `start`/`end`（循环时返回 `cycle` 对象） |
| `PUT` | `/api/shows/{id}` | 重命名/改说明（可部分更新） |
| `DELETE` | `/api/shows/{id}` | 删除整场演出（级联删除提示） |
| `POST` | `/api/shows/{id}/cues` | 新增提示 |
| `PUT` | `/api/cues/{id}` | 更新提示（循环返回 409 + `chain`） |
| `DELETE` | `/api/cues/{id}` | 删除提示（仍被其他提示依赖时返回 400） |
| `GET` | `/api/export` | 导出全部演出为 JSON（附件下载） |
| `GET` | `/api/shows/{id}/export` | 导出单场演出 |
| `POST` | `/api/import` | 导入 JSON；校验失败整体回滚 |

## 导入 / 导出 JSON 格式

依赖关系通过**编号** `predecessor_number` 引用，因此导入文件中调整提示顺序也不会错位：

```json
{
  "format": "stagecue-json",
  "version": 1,
  "shows": [
    {
      "name": "夜航",
      "description": "可选说明",
      "cues": [
        {
          "number": "L1",
          "cue_type": "light",
          "title": "开场暖光",
          "notes": "面光渐亮至 60%",
          "start_mode": "fixed",
          "fixed_seconds": 0,
          "predecessor_number": null,
          "delay_seconds": 0,
          "duration_seconds": 30
        },
        {
          "number": "S1",
          "cue_type": "sound",
          "title": "开场音乐",
          "notes": "",
          "start_mode": "after",
          "fixed_seconds": null,
          "predecessor_number": "L1",
          "delay_seconds": 0,
          "duration_seconds": 60
        }
      ]
    }
  ]
}
```

- `cue_type` ∈ `light` | `sound` | `scene`
- `start_mode` ∈ `fixed`（使用 `fixed_seconds`）| `after`（使用 `predecessor_number` + `delay_seconds`）
- 所有时间单位均为**秒**，支持小数（如 `0.5`）；开始时间 = 前置提示开始时间 + 前置时长 + 延迟。
- 也接受直接传单个演出对象（含 `name` 和 `cues`，不带 `shows` 外壳）。

## 排程与循环检测说明

- 固定提示构成时间锚点；依赖链 `开始 = 前置.开始 + 前置.duration + delay`。
- 算法为沿依赖边的 DFS（`app/scheduler.py`，前端 `static/scheduler.js` 是同构镜像，两边规则一致）。
- 遇到回边即判定循环，返回从回边起点闭合的完整链；前端时间轴与列表均会标红提示。

## 目录结构

```
.
├── Dockerfile
├── compose.yaml
├── docker-entrypoint.sh   # 建库/播种 + 启动 gunicorn
├── requirements.txt
├── app
│   ├── server.py          # Flask 路由、校验、导入导出
│   ├── scheduler.py       # 时间计算与循环检测
│   ├── db.py              # SQLite 连接、建表、演示数据
│   └── static
│       ├── index.html
│       ├── style.css
│       ├── scheduler.js   # 前端同构排程算法
│       └── app.js         # 列表、时间轴、拖拽、弹窗、导入导出
└── smoke_test.py          # 后端端到端冒烟测试（可选运行）
```

## 测试

```bash
python3 smoke_test.py      # 覆盖排程联动、循环 409、回滚、导入导出等场景
```
