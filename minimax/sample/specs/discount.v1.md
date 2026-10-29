# 折扣规格 v1（历史版本）

本文档中的条目 ID 用于任务上下文引用，格式为 `SPEC-<编号>`。

## SPEC-01 整单折扣

`apply_discount(items, rate=0.9)` 对订单总额应用整单折扣，返回两位小数金额。

- `rate` 必须落在 `(0, 1]` 区间，否则抛出 `ValueError`。
- 金额一律四舍五入（HALF_UP）保留两位小数。

## SPEC-02 金额门槛筛选（未定义，v1 不支持）

v1 **不提供**按订单金额门槛筛选的能力：

- `apply_discount` 只有 `items` 与 `rate` 两个参数，不存在 `min_total`。
- 无论订单总额多少，只要调用 `apply_discount` 就会应用折扣。
- 需求方提出的 min_total 门槛筛选属于 v2 规划，尚未进入实现范围。

## SPEC-03 兼容性

`line_total`、`order_total`、`apply_discount` 的既有签名与返回值不得改变。
