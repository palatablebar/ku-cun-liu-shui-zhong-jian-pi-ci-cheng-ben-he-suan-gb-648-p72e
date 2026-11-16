# 深度登记 stockflow

- 规模：中型（7 个功能模块 / 7 个页面 / 73 条测试 / 12 条 TRAP）
- 核心规则：按 `(发生时间, 单据号, 行号)` 全序重放；出库超过在手整行隔离且不压负库存；调拨必须双边同料异仓等量；入库建 FIFO 层、出库吃最早层、退货带原单回原层、无原单建新层；成本 = 消耗层金额之和（Decimal）。
- 使用者与工作流：区域仓 3 个仓、900 个物料的库存核算员，每月把三套系统导出的出入库流水、期初余额与实盘表放进来，一次运行得到采纳/隔离/层/成本/盘点差异与可导出的证据行。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 流水采信 | 导入流水或期初，看到编码、分段、列数与原始行号 | POST /api/movements/import、页面 /movements | `app/modules/m1_ingest/reader.py` `app/modules/m1_ingest/decode.py` | `tests/test_m1_ingest.py` |
| M2 单据规则 | 查列定义、方向、单价义务与单位换算 | GET /api/contract | `app/modules/m2_doc_rules/contract.py` `app/modules/m2_doc_rules/coerce.py` | `tests/test_m2_doc_rules.py` |
| M3 质量隔离 | 按原因码看隔离行并导出证据 CSV | GET /api/quarantine、GET /api/quarantine.csv | `app/modules/m3_quality/rules.py` `app/modules/m3_quality/quarantine.py` | `tests/test_m3_quality.py` |
| M4 时序重放 | 重放流水，看超库存拒绝、调拨配对与回原层 | POST /api/replay | `app/modules/m4_replay/replay.py` `app/modules/m4_replay/fifo.py` | `tests/test_m4_replay.py` |
| M5 FIFO 成本 | 看层、按单据出库成本与金额恒等式 | GET /api/cost、页面 /cost | `app/modules/m5_cost/cogs.py` `app/modules/m5_cost/layers.py` | `tests/test_m5_cost.py` |
| M6 盘点对账 | 看六本账、提交实盘、按版本闸门拒旧版本 | GET /api/reconcile、POST /api/reconcile/counts | `app/modules/m6_reconcile/counts.py` `app/modules/m6_reconcile/balance.py` | `tests/test_m6_reconcile.py` |
| M7 导出 | 导出流水证据行、层、成本与运行清单 | GET /api/export/movements.csv、页面 /export | `app/modules/m7_export/csv_out.py` `app/modules/m7_export/manifest_out.py` | `tests/test_m7_export.py` |

端到端：`tests/test_e2e.py`（脏样例 → 采信 → 质量 → 重放 → 成本 → 盘点 → 导出；含 6 个页面 200、幂等重跑、干净样例同路径与失败输入）；不变量：`tests/test_invariants.py`（层守恒、层边界、调拨金额对称、盘点调整守恒、优先级置换）。

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-05 | 行尾多一列、列数不齐 | 静默补空或错列错位 | 严格列数校验，命中 `COLUMN_COUNT` 进隔离并保留原值 | `app/modules/m1_ingest/reader.py` | `tests/test_m1_ingest.py::test_column_count_row_is_flagged` |
| TRAP-06 | 数量写成 `1,200`、`１２００`、`1,000g` | `Decimal()` 直接抛错或把全角当单位 | NFKC 转半角、去千分位与单位后缀，再 Decimal | `app/modules/m2_doc_rules/coerce.py` | `tests/test_m2_doc_rules.py::test_thousands_fullwidth_and_suffix` |
| TRAP-07 | 数量列是 `-` / `N/A` / 空串 | 被当成 0 参与合计 | 哨兵表集中定义，返回 None 并进隔离 | `app/config.py`、`app/modules/m2_doc_rules/coerce.py` | `tests/test_m2_doc_rules.py::test_sentinels_are_not_zero` |
| TRAP-08 | 物料编码 `000123` 与 `123` | `int()` 化后两个物料被合并 | 编码全程字符串，导出逐字保留 | `app/modules/m2_doc_rules/coerce.py`、`app/modules/m7_export/csv_out.py` | `tests/test_m2_doc_rules.py::test_leading_zero_sku_kept_as_string` |
| TRAP-10 | 22 个层金额用 float 累加 | 合计与逐层复算差 0.01 | 数量与金额一律 Decimal，金额保存精确乘积 | `app/modules/m4_replay/replay.py`、`app/modules/m5_cost/cogs.py` | `tests/test_m5_cost.py::test_cogs_is_exact_decimal` |
| TRAP-13 | 导出文件行序与发生时间相反 | 按文件顺序重放，出现假负库存 | 全序 `(发生时间, 单据号, 行号)` 重放，乱序输入结果不变 | `app/modules/m4_replay/replay.py` | `tests/test_m4_replay.py::test_replay_totals_survive_input_permutation` |
| TRAP-14 | 同一单据同版本两个数量 | 后写覆盖，结果随文件顺序变化 | 高版本覆盖低版本；同版本不同取值整组隔离 | `app/modules/m3_quality/rules.py` | `tests/test_m3_quality.py::test_same_revision_conflict_quarantined` |
| TRAP-15 | 同一物料 g 与 kg 混用 | 合计差 1000 倍 | 单位列 + 换算系数，先折 kg 再算 | `app/config.py`、`app/modules/m2_doc_rules/coerce.py` | `tests/test_m2_doc_rules.py::test_unit_conversion_g_to_kg` |
| TRAP-16 | 末行没有换行且列数不足 | 半行被当成有效入库 | 末行缺列且无换行判 `TRUNCATED_TAIL` | `app/modules/m1_ingest/reader.py` | `tests/test_m1_ingest.py::test_truncated_tail_rejected` |
| TRAP-19 | 一行同时命中列数、单位、数量三类问题 | 取哪条原因码取决于检查顺序 | 原因码优先级表固定，取最高一条 | `app/reasons.py`、`app/modules/m3_quality/rules.py` | `tests/test_m3_quality.py::test_priority_order_is_stable` |
| TRAP-21 | 调拨只有一条边，或两边数量不等 | 单边也建层，凭空多出库存 | 按单据号配对：缺边、异料、同仓、数量不等整组隔离 | `app/modules/m4_replay/replay.py` | `tests/test_m4_replay.py::test_transfer_missing_leg_quarantined` |
| TRAP-26 | 旧版本盘点提交覆盖新盘点 | 后提交无条件覆盖 | 版本闸门：旧版本 409，同版本不同内容 409，同版本同内容幂等 | `app/modules/m6_reconcile/counts.py`、`app/modules/m6_reconcile/service.py` | `tests/test_m6_reconcile.py::test_stale_revision_rejected` |

## 对账规则

- 行数账：原始行 = 采纳 + 隔离 + 被取代 + 重复（空行单独计数，表头分段单独计数）。
- 数量账：期初 + 入 − 出 + 调整入 − 调整出 = 期末；入含调拨入与退货，出含调拨出。
- 分层账：Σ 层剩余数量 = 期末数量；Σ 层金额 = 期末金额。
- 守恒账：Σ 建层数量 − 消耗 + 恢复 = Σ 层剩余；金额同理（逐层算术恒等）。
- 成本账：出库成本 = 消耗金额 − 退货恢复金额。
- 调拨账：调拨出数量 = 调拨入数量（成对才应用）。
- 盘点账：实盘 = 系统数量 + 差异；重放期末 + 盘点调整 = 盘点后在手。
- 八本账在 `/api/reconcile`、页面 `/reconcile`、运行清单的 `checks` 中同源展示。

## 已知不覆盖

- 不做跨期结转：期初只来自 `opening_balance.csv`，上一个月的期末不会自动滚入。
- 不支持按件计量的物料：单位只接受 kg 与 g，`件` 进隔离。
- 调拨入边成本按消耗金额除数量后量化到四位，跨多个不同成本层时会出现 0.0001 级尾差，未做尾差再分配。
- 不做并发写入控制：SQLite 单进程使用，同一批次重跑走先删后插。
- 历史批次的流水文件若已从 `sample_data/` 移除，`POST /api/replay` 会返回 409，需要重新导入。
