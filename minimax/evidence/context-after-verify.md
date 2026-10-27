# SDD 开发上下文

- 任务：订单满 99 元免运费；会员折扣后金额不足 99 元时应按折后金额判断，金卡会员始终免运费。
- 上下文运行：`ctx-20261028010338216`（2026-10-28T01:03:38+08:00）
- 材料索引后端：`seekdb`
- 主条款：S-04

## 适用规范条款

### S-04 运费

来源：`workspace/specs/pricing_rules.md:25-28`　匹配分：23

订单包邮门槛按“会员折扣后金额”判断：折后金额大于等于 99.00 元时免运费；低于
99.00 元收取 8.00 元运费。`gold` 会员始终免运费。

### S-02 会员折扣

来源：`workspace/specs/pricing_rules.md:14-18`　匹配分：14

会员等级分为 `gold`、`silver`、`none`。`gold` 享受小计 10% 的折扣，`silver` 享受
5% 的折扣，`none` 不打折。折扣金额按四舍五入保留两位小数。未知会员等级应抛出
`ValueError`。

### S-03 优惠券

来源：`workspace/specs/pricing_rules.md:20-23`　匹配分：11

优惠券金额在会员折扣之后扣减。扣减后应付金额不得为负数：若优惠券金额超过折后
金额，应付金额取 0。优惠券金额不得为负数。

### S-01 订单小计

来源：`workspace/specs/pricing_rules.md:8-12`　匹配分：6

订单小计为所有商品行 `单价 × 数量` 之和。金额使用 Decimal 计算，并按四舍五入
（ROUND_HALF_UP）保留两位小数。商品行缺少 `unit_price` 或 `quantity` 时应抛出
`ValueError`，不得静默跳过。

### S-05 应付总额

来源：`workspace/specs/pricing_rules.md:30-33`　匹配分：5

应付总额 = max(0, 小计 − 会员折扣 − 优惠券金额) + 运费。最终结果保留两位小数，
返回值包含 `subtotal`、`member_discount`、`coupon`、`shipping`、`payable` 五个字段。

## 需要修改的代码

- `shipping`（function）— `workspace/src/pricing/engine.py:60-69`  匹配分 5，docstring references S-04
  - 签名：`shipping(amount_before_discount: Decimal, tier: str, amount_after_discount: Decimal)`
  - 说明：Return the shipping fee for an order (S-04).

## 功能测试（必须由本次变更转为通过）

- `test_shipping_is_free_at_the_exact_threshold` — `workspace/tests/test_shipping.py:13-17`（covers S-04）
- `test_shipping_uses_the_post_discount_amount` — `workspace/tests/test_shipping.py:20-24`（covers S-04）
- `test_gold_members_always_get_free_shipping` — `workspace/tests/test_shipping.py:27-31`（covers S-04）

## 兼容性 / 回归测试（变更后必须保持通过）

- `test_subtotal_multiplies_unit_price_by_quantity` — `workspace/tests/test_engine.py:14-22`（covers S-01）
- `test_subtotal_rounds_half_up_to_cents` — `workspace/tests/test_engine.py:25-28`（covers S-01）
- `test_order_line_without_price_is_rejected` — `workspace/tests/test_engine.py:31-34`（covers S-01）
- `test_gold_member_receives_ten_percent_discount` — `workspace/tests/test_engine.py:37-40`（covers S-02）
- `test_unknown_member_tier_is_rejected` — `workspace/tests/test_engine.py:43-46`（covers S-02）
- `test_coupon_never_makes_the_order_negative` — `workspace/tests/test_engine.py:49-53`（covers S-03）
- `test_coupon_is_applied_after_the_member_discount` — `workspace/tests/test_engine.py:56-60`（covers S-03）
- `test_payable_is_subtotal_plus_shipping` — `workspace/tests/test_engine.py:63-67`（covers S-05）

## 任务状态（PowerContext）

- Scope：`scp_77srg4mj6z0xk1xhkhw54xeac0`；Artifact：`art_f7246e11cc7d4b87ab26c1fa2a8caa5e`；版本：2
- 状态：`verified`

已完成：

- 材料已导入并完成上下文选择（ctx-20261028010330313）
- 目标测试全部通过（3 个）
- 回归测试全部通过（8 个）

待完成：

- （无）

已有验证结果：

- target tests (3 expected)：3 passed, 0 failed（`/home/rst/sdd-conda/bin/python -m pytest -v -rA tests`，2026-10-28T01:03:30+08:00）
- regression tests (8 expected)：8 passed, 0 failed（`/home/rst/sdd-conda/bin/python -m pytest -v -rA tests`，2026-10-28T01:03:30+08:00）
