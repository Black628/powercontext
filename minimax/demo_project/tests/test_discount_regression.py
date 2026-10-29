"""回归测试：v1 既有行为，任何修改后都必须保持通过。"""

import unittest

from inventory import apply_discount, line_total, order_total


class DiscountRegressionTest(unittest.TestCase):
    def test_line_total(self):
        self.assertEqual(line_total({"price": 19.99, "qty": 3}), 59.97)

    def test_order_total_sums_lines(self):
        order = [
            {"price": 10.0, "qty": 2},
            {"price": 5.5, "qty": 1},
        ]
        self.assertEqual(order_total(order), 25.5)

    def test_apply_discount_default_rate(self):
        self.assertEqual(apply_discount([{"price": 100.0, "qty": 1}]), 90.0)

    def test_apply_discount_custom_rate(self):
        self.assertEqual(apply_discount([{"price": 100.0, "qty": 1}], rate=0.75), 75.0)

    def test_invalid_rate_raises(self):
        for bad in (0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                apply_discount([{"price": 100.0, "qty": 1}], rate=bad)

    def test_rounding_half_up_two_decimals(self):
        # 10.005 * 0.9 = 9.0045 -> 9.0 ; 11.15 * 1.0 -> 11.15
        self.assertEqual(apply_discount([{"price": 11.15, "qty": 1}], rate=1.0), 11.15)
        self.assertEqual(apply_discount([{"price": 33.33, "qty": 1}], rate=0.9), 30.0)

    def test_empty_order(self):
        self.assertEqual(order_total([]), 0.0)
        self.assertEqual(apply_discount([]), 0.0)


if __name__ == "__main__":
    unittest.main()
