"""Order settlement engine.

The business rules implemented here are specified in ``workspace/specs/pricing_rules.md``.
Each function cites the clause it implements so that spec changes are easy to trace.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

FREE_SHIPPING_THRESHOLD = Decimal("99.00")  # S-04
SHIPPING_FEE = Decimal("8.00")  # S-04

MEMBER_DISCOUNT_RATES = {  # S-02
    "gold": Decimal("0.10"),
    "silver": Decimal("0.05"),
    "none": Decimal("0.00"),
}


def money(value: Any) -> Decimal:
    """Round ``value`` to two decimal places using half-up rounding (S-01)."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _item_amount(item: Mapping[str, Any]) -> Decimal:
    try:
        unit_price = item["unit_price"]
        quantity = item["quantity"]
    except KeyError as exc:
        raise ValueError(f"order line is missing {exc.args[0]!r}") from exc
    if quantity < 0:
        raise ValueError("order line quantity must not be negative")
    return money(Decimal(str(unit_price)) * int(quantity))


def subtotal(items: Iterable[Mapping[str, Any]]) -> Decimal:
    """Return the sum of ``unit_price * quantity`` over all order lines (S-01)."""
    return money(sum((_item_amount(item) for item in items), Decimal("0")))


def member_discount(total: Decimal, tier: str) -> Decimal:
    """Return the member discount for ``tier`` applied to ``total`` (S-02)."""
    try:
        rate = MEMBER_DISCOUNT_RATES[tier]
    except KeyError as exc:
        raise ValueError(f"unknown member tier {tier!r}") from exc
    return money(total * rate)


def amount_after_discount(total: Decimal, tier: str) -> Decimal:
    """Return the non-negative amount left after the member discount (S-03, S-05)."""
    return money(max(ZERO, total - member_discount(total, tier)))


def shipping(amount_before_discount: Decimal, tier: str, amount_after_discount: Decimal) -> Decimal:
    """Return the shipping fee for an order (S-04).

    The free-shipping decision is taken from the pre-discount amount and uses a
    strict comparison, so orders that only reach the threshold after a discount
    are charged shipping.  ``tier`` is accepted but not yet used.
    """
    if amount_before_discount > FREE_SHIPPING_THRESHOLD:
        return ZERO
    return SHIPPING_FEE


def settle(
    items: Iterable[Mapping[str, Any]],
    tier: str = "none",
    coupon: Decimal | str | int = ZERO,
) -> dict[str, Decimal]:
    """Settle an order and return its amount breakdown (S-05)."""
    total = subtotal(items)
    discount = member_discount(total, tier)
    after_discount = amount_after_discount(total, tier)

    coupon_amount = money(coupon)
    if coupon_amount < ZERO:
        raise ValueError("coupon amount must not be negative")

    payable = money(max(ZERO, after_discount - coupon_amount))
    fee = shipping(total, tier, after_discount)

    return {
        "subtotal": total,
        "member_discount": discount,
        "coupon": coupon_amount,
        "shipping": fee,
        "payable": money(payable + fee),
    }
