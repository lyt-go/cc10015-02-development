# 多维分析实验平台

面向实验设计、指标计算、样本分析、效果评估与结论发布的数据分析实验平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实验设计 | `experiment` | 实验方案 | 方案编号、实验名称、对照组说明 |
| 样本分群 | `sample_group` | 样本分群 | 分群编号、分群名称、筛选条件 |
| 指标定义 | `metric_def` | 指标定义 | 指标编号、指标名称、计算公式 |
| AB结果分析 | `ab_result` | 分析报告 | 报告编号、实验编号、分析指标 |
| 分群对比 | `segment_compare` | 对比任务 | 任务编号、分群A、分群B |
| 多维下钻 | `drill_down` | 下钻维度 | 维度编号、维度名称、下钻层级 |
| 数据筛选 | `data_filter` | 筛选规则 | 规则编号、数据来源、筛选字段 |
| 归因分析 | `attr_analysis` | 归因任务 | 任务编号、目标指标、候选因子 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则编号、监控指标、检测算法 |
| 预测模型 | `forecast_model` | 模型配置 | 模型编号、预测对象、历史窗口 |
| 数据导出 | `data_export` | 导出任务 | 任务编号、数据来源、导出格式 |
| 同期群追踪 | `cohort_track` | 同期群定义 | 群组编号、群组名称、首次行为 |
| 可视化配置 | `chart_config` | 图表配置 | 图表编号、图表类型、数据来源 |
| 洞察报告 | `insight_report` | 洞察报告 | 报告编号、分析主题、数据来源 |
| 洞察结论数据表 | `insight_conclusion` | 洞察结论（V002） | 结论编号、报告版本、实验编号、指标标识、口径版本、证据来源、证据快照 |
| 数据新鲜度 | `data_freshness` | 新鲜度监控 | 监控编号、数据表名、最新分区 |
| 口径注册 | `metric_alias` | 口径版本 | 版本编号、指标编号、变更说明 |
| 实验因子 | `expr_factor` | 因子配置 | 因子编号、因子名称、因子层级 |
| 评估看板 | `evaluate_board` | 看板配置 | 看板编号、看板名称、关联实验 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 洞察报告 V002：洞察结论数据表

洞察报告不再只给模拟看板数字，而是读取 AB 对比结果表（`ab_result`）与异常检测信号表
（`anomaly_detect`），把关键发现沉淀为可追溯的结论行，写入 `insight_conclusion` 表。
每条结论都带「实验编号 + 指标标识 + 口径版本 + 证据来源 + 证据快照 + 报告版本」，
因此任何一条关键发现都能回查同一实验、同一指标口径与当时的证据。

### 主要接口

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/insight_report/conclusions` | 洞察结论数据表，支持 `report_id` 过滤（工作台与详情页同源） |
| `GET /api/insight_report/conclusions/overview` | 结论总数、章节/证据来源分布、口径版本覆盖率 |
| `POST /api/insight_report/{id}/conclusions/generate` | 按章节生成/续写结论，`strategy=time|chapter` |
| `POST /api/insight_report/conclusions/enrich-caliber` | V002 联动：按指标标识补齐口径版本 |
| `POST /api/metric_def/sync-conclusion-caliber` | 指标定义侧发起的同一条 V002 联动 |
| `GET /api/insight_report/conclusions/audit` | 事务回滚、中断续写、版本保护的留档核对记录 |

### 关键规则

- **证据与版本同写**：结论落库前逐行校验证据快照、证据来源与报告版本，任一缺失即整体回滚，
  不会出现“页面已完成但证据行缺失”。
- **断点续写**：生成中断只保留已完成章节；重试仅补齐未完成章节，已完成结论不重写。
- **不覆盖完整结论**：一份报告已有完整结论时重试，会开启新报告版本（`V002-r2`…）并留档，
  上一份结论原样保留。
- **残留事务回滚**：结论表按章节处理时发现本报告的未清残留，V002 下整批回滚、清除残留并留档核对。
- **版本冲突两种收敛**：同一实验同时存在 AB 结果与异常信号时，
  `time` 按证据时间收敛为单条综合发现（保留两个来源时间），
  `chapter` 按报告章节保留两项发现并统一报告版本。
- **跨模块联动**：指标定义模块读取结论表中的指标标识，为关键发现补齐口径版本（幂等）。

领域：数据工程
子方向：数据分析与数据应用
