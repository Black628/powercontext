"""订单金额与折扣计算（演示工程，无第三方依赖）。"""

from decimal import Decimal, ROUND_HALF_UP


def _money(value):
    """按四舍五入保留两位小数（业务口径：银行家舍入禁止，统一 HALF_UP）。"""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def line_total(item):
    """单行小计 = 单价 * 数量。"""
    return _money(item["price"] * item["qty"])


def order_total(items):
    """订单总额 = 所有行小计之和。"""
    return _money(sum(line_total(item) for item in items))


def apply_discount(items, rate=0.9):
    """对订单应用整单折扣，返回折扣后金额（两位小数）。

    rate: (0, 1] 区间的折扣系数，1 表示不打折。
    """
    if not 0 < rate <= 1:
        raise ValueError("rate must be in (0, 1]")
    return _money(order_total(items) * rate)
