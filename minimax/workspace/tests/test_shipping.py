"""Target tests for the S-04 free-shipping rule.

These tests describe the behaviour that the specification asks for and that the
current implementation does not provide yet. They must fail before the code
change and pass afterwards.
"""

from decimal import Decimal

from pricing.engine import settle


def test_shipping_is_free_at_the_exact_threshold():
    """Covers: S-04. A post-discount amount of exactly 99.00 qualifies for free shipping."""
    result = settle([{"unit_price": "99.00", "quantity": 1}], tier="none")
    assert result["shipping"] == Decimal("0.00")
    assert result["payable"] == Decimal("99.00")


def test_shipping_uses_the_post_discount_amount():
    """Covers: S-04. A 100.00 order with a silver discount drops to 95.00 and pays shipping."""
    result = settle([{"unit_price": "100.00", "quantity": 1}], tier="silver")
    assert result["shipping"] == Decimal("8.00")
    assert result["payable"] == Decimal("103.00")


def test_gold_members_always_get_free_shipping():
    """Covers: S-04. Gold membership waives the shipping fee for small orders."""
    result = settle([{"unit_price": "50.00", "quantity": 1}], tier="gold")
    assert result["shipping"] == Decimal("0.00")
    assert result["payable"] == Decimal("45.00")
