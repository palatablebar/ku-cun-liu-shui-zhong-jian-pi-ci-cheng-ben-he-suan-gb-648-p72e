# 运行手册 stockflow

## 启动

```bash
docker compose up -d --build
curl -s localhost:18207/api/health
```

首页 `http://localhost:18207/` 打开后点「重跑」执行样例主流程；所有页面共用同一批次。

## 重建数据库

```bash
docker compose down -v
docker compose up -d --build
```

`down -v` 删除命名卷 `stockflow-data`；启动后第一次运行生成新库。

## 主流程

```bash
curl -s -X POST localhost:18207/api/demo/run | python3 -m json.tool | head -40
curl -s localhost:18207/api/quarantine.csv | head -5
curl -s localhost:18207/api/replay -X POST
curl -s localhost:18207/api/cost | python3 -m json.tool | head -30
curl -s localhost:18207/api/reconcile | python3 -m json.tool | head -40
curl -s localhost:18207/api/export/manifest.json
```

## 导入自己的文件

```bash
curl -s -F sample=movements_clean.csv localhost:18207/api/movements/import
curl -s -F file=@/path/to/movements.csv localhost:18207/api/movements/import
curl -s -F file=@/path/to/opening.csv localhost:18207/api/movements/opening
```

导入只接受与样例同列名（或登记过的英文别名）的文件；列名不对时首行会被判表头缺失。

## 版本裁决：迟到的修订怎么入账

同一张单据行的新版本迟到时，系统在「静默沿用旧版」与「停用整张单据」之间取行级裁决：

```bash
curl -s localhost:18207/api/revisions | python3 -m json.tool
curl -s localhost:18207/api/revisions.csv | head -5
```

- 新版本干净：采纳最高版本，旧版本记 `SUPERSEDED`（被取代）。
- 新版本数量改坏 / 字段缺失：新版本记 `REVISION_REJECTED` 进隔离区，回退采纳最近一条干净旧版并打 `FALLBACK_APPLIED`；同单据其他行照常入账，不拖累成本核算。
- 没有干净旧版可回退：整组隔离（`rejected_all`），谁都不信。
- 同版本两个取值冲突：整组隔离（`DUP_DOC_CONFLICT`）。
- 「影响」列是反事实重放差额，口径为「若改采纳判废新版本 − 当前实际入账」；新版本数量本身不可解析时不臆造数字，影响留空。
- 所有版本（判废 / 被取代 / 重复 / 回退采纳）都保留原值与文件行号，可在 `/movements`、隔离 CSV 与 `revisions.csv` 追溯；同批输入重跑 run_id 与裁决结论完全一致。

## 盘点提交与版本

```bash
curl -s -X POST localhost:18207/api/reconcile/counts \
  -H 'Content-Type: application/json' \
  -d '{"count_no":"PD-C-05","revision":2,"rows":[{"sku":"000123","warehouse":"WH-A","counted_qty":"500","unit":"kg"}]}'
```

- 旧版本返回 409 `E_STALE_COUNT_REVISION`；同版本不同内容返回 409 `E_COUNT_REVISION_CONFLICT`。
- 同版本同内容幂等，重复提交不新增盘点行。
- 盘亏超过在手返回记录状态 `COUNT_SHORT`，不做负库存。

## 测试

```bash
docker compose exec -T app python -m pytest -q
```

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 首页 200 但数字全 0 | 还没跑批次 | 点「重跑」或 POST `/api/demo/run` |
| 导入回报 404 | 样例名不在 `sample_data/` | 用上传文件或核对样例名 |
| `POST /api/replay` 409 | 批次登记的源文件已不在样例目录 | 重新导入流水 |
| 盘点提交 409 | 版本旧于已记录版本 | 升版本号再提交 |
| 流水行带「回退采纳」标记 | 新版本判废，本行作为干净旧版被回退入账 | 到「版本裁决」页看判废原因与成本/库存影响 |
| 层金额与期末金额不符 | 有人直接改库 | 重跑批次会先删后插，恢复一致 |
