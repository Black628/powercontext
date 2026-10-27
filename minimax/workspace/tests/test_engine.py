"""Regression tests for the pricing settlement service.

These tests describe behaviour that is already implemented and must keep
working while the S-04 shipping rule is completed.
"""

from decimal import Decimal

import pytest

from pricing.engine import settle


def test_subtotal_multiplies_unit_price_by_quantity():
    """Covers: S-01. The subtotal is the sum of all order lines."""
    result = settle(
        [
            {"unit_price": "12.50", "quantity": 3},
            {"unit_price": "4.00", "quantity": 2},
        ]
    )
    assert result["subtotal"] == Decimal("45.50")


def test_subtotal_rounds_half_up_to_cents():
    """Covers: S-01. Three-decimal prices are rounded half-up."""
    result = settle([{"unit_price": "1.005", "quantity": 2}])
    assert result["subtotal"] == Decimal("2.01")


def test_order_line_without_price_is_rejected():
    """Covers: S-01. Incomplete order lines fail loudly instead of being skipped."""
    with pytest.raises(ValueError):
        settle([{"quantity": 1}])


def test_gold_member_receives_ten_percent_discount():
    """Covers: S-02. Gold membership discounts the subtotal by ten percent."""
    result = settle([{"unit_price": "200.00", "quantity": 1}], tier="gold")
    assert result["member_discount"] == Decimal("20.00")


def test_unknown_member_tier_is_rejected():
    """Covers: S-02. Unsupported membership levels are rejected."""
    with pytest.raises(ValueError):
        settle([{"unit_price": "10.00", "quantity": 1}], tier="platinum")


def test_coupon_never_makes_the_order_negative():
    """Covers: S-03. A coupon larger than the amount due yields zero, not a credit."""
    result = settle([{"unit_price": "30.00", "quantity": 1}], coupon=Decimal("50.00"))
    assert result["coupon"] == Decimal("50.00")
    assert result["payable"] == Decimal("8.00")


def test_coupon_is_applied_after_the_member_discount():
    """Covers: S-03. The coupon is deducted from the post-discount amount."""
    result = settle([{"unit_price": "150.00", "quantity": 1}], tier="gold", coupon=Decimal("5.00"))
    assert result["member_discount"] == Decimal("15.00")
    assert result["payable"] == Decimal("130.00")


def test_payable_is_subtotal_plus_shipping():
    """Covers: S-05. All amount fields are returned with two decimals."""
    result = settle([{"unit_price": "150.00", "quantity": 1}], tier="none")
    assert result["shipping"] == Decimal("0.00")
    assert result["payable"] == Decimal("150.00")
