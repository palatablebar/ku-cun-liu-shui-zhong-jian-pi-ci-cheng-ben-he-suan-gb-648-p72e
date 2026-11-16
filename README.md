# stockflow 库存流水重建与批次成本核算

区域仓 3 个仓、900 个物料的库存核算工具：把三套系统导出的出入库流水、期初余额与实盘表放进来，
一次运行得到逐行诊断、FIFO 层、按单据出库成本、盘点差异与可导出的证据行。

## 能力

- 采信：编码回退链（UTF-8 BOM / UTF-8 / GB18030）、列名别名、重复表头分段、列数校验、末行截断、原始行号。
- 单据规则：六种单据类型的方向与单价义务，kg / g 换算，千分位与全角数字，空值哨兵，时间归一 UTC。
- 质量隔离：18 类原因码，同一业务键按版本裁决，隔离行可导出 CSV（含原值与行号）。
- 时序重放：全序重放，出库超在手整行拒绝后继续，调拨必须双边，退货可回原层。
- FIFO 成本：层成本、层金额、按单据净成本，金额全部 Decimal。
- 盘点对账：八本账逐项对照，实盘差异生成调整层，版本闸门拒绝旧提交。
- 导出：流水证据行、FIFO 层、单据成本 CSV 与带输入 sha256 的运行清单。

## 模块

| 模块 | 动作 | 入口 |
|---|---|---|
| M1 流水采信 | 导入流水 / 期初，看编码与分段诊断 | `POST /api/movements/import`、`POST /api/movements/opening` |
| M2 单据规则 | 查方向、单价义务、单位与量化刻度 | `GET /api/contract` |
| M3 质量隔离 | 看隔离行与原因码分布 | `GET /api/quarantine`、`GET /api/quarantine.csv` |
| M4 时序重放 | 单独重跑重放 | `POST /api/replay`、`GET /api/replay` |
| M5 FIFO 成本 | 看层与出库成本 | `GET /api/cost`、`GET /api/cost/layers` |
| M6 盘点对账 | 看八本账、提交实盘 | `GET /api/reconcile`、`POST /api/reconcile/counts` |
| M7 导出 | 导出 CSV 与运行清单 | `GET /api/export/{movements.csv,layers.csv,cost.csv,manifest.json}` |

## 核心规则

1. 全序按 `(发生时间 UTC, 单据号, 行号, 物料, 仓库)` 排，输入乱序不影响结果。
2. 出库 / 调拨出 / 调整出合计超过在手时，整行隔离，不做部分消耗、不压负库存，后续行继续重放。
3. 调拨按单据号配对：一条出边一条入边、同物料、异仓、数量相等；缺边、异料、同仓、数量不等整组隔离。
4. 入库建层；出库按层创建顺序消耗；退货带原单号按原消耗顺序回原层，无原单号按本次单价建新层。
5. 出库成本 = 消耗层金额之和 − 退货恢复金额，`Decimal` 精确计算。
6. 盘点差异：盘盈按最新层成本建调整层，盘亏按 FIFO 消耗，盘亏超过在手记拒绝。

## 定义与已知不覆盖

- 列、单位、精度、空值、去重键、空态的定义见 `docs/DATA-DICTIONARY.md`。
- 深度登记（模块表、TRAP 表、对账规则、覆盖边界）见 `docs/DEPTH.md`。
- 不覆盖跨期结转、按件计量、调拨尾差再分配与并发写控制。

## 启动

```bash
docker compose up -d --build
```

- 页面：`http://localhost:18207/`
- 健康检查：`http://localhost:18207/api/health`
- 接口文档：`http://localhost:18207/docs`

样例数据随仓库自带，启动后点首页「重跑」或 `curl -X POST localhost:18207/api/demo/run` 即可跑通主流程。

## 演示步骤

1. 首页看批次号、六项指标、运行日志与八本账。
2. 「流水」页按状态筛隔离行，点「隔离 CSV」导出证据。
3. 「批次层」页看每个物料每个仓库的层与层金额。
4. 「成本」页看按单据净成本与三条恒等式。
5. 「盘点」页看盘点差异、提交实盘；旧版本提交会被拒绝。
6. 「导出」页下载流水、层、成本与运行清单。

## 目录

```text
app/
  main.py config.py errors.py reasons.py manifest.py store.py web.py pipeline.py
  modules/m1_ingest … m7_export   每个模块 handler + service + 2 个规则/读写文件
  templates/  base + 6 页
  static/     theme.css layout.css components.css app.js table.js console.js
docs/         DEPTH.md DATA-DICTIONARY.md RUNBOOK.md
sample_data/  7 个样例（含脏样例、截断、仅表头、空文件）
tests/        conftest + 每模块一个测试文件 + e2e + invariants + fixtures
Dockerfile docker-compose.yml requirements.txt .env.example .dockerignore pytest.ini
```
