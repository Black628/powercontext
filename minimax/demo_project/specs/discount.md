# 折扣规格 v2（当前生效）

本文档中的条目 ID 用于任务上下文引用，格式为 `SPEC-<编号>`。

## SPEC-01 整单折扣

`apply_discount(items, rate=0.9)` 对订单总额应用整单折扣，返回两位小数金额。

- `rate` 必须落在 `(0, 1]` 区间，否则抛出 `ValueError`。
- 金额一律四舍五入（HALF_UP）保留两位小数。

## SPEC-02 金额门槛筛选（min_total，v2 新增）

`apply_discount` 增加关键字参数 `min_total=None`：

```python
apply_discount(items, rate=0.9, min_total=None)
```

行为要求：

1. `min_total is None`：保持 v1 行为，整单应用折扣。
2. `order_total(items) < min_total`：**不应用折扣**，直接返回订单总额（两位小数）。
3. `order_total(items) >= min_total`：正常应用折扣，返回 `order_total(items) * rate`。
4. `min_total` 必须为正数（`min_total > 0`），否则抛出 `ValueError`。
5. 门槛判断一律使用折扣前的订单总额 `order_total(items)`。

## SPEC-03 兼容性

除 `min_total` 之外，`line_total`、`order_total`、`apply_discount` 的既有签名与返回值不得改变，
存量调用 `apply_discount(items)` 与 `apply_discount(items, rate)` 必须继续按 v1 语义工作。
