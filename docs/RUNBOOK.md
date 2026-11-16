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
| 层金额与期末金额不符 | 有人直接改库 | 重跑批次会先删后插，恢复一致 |
