"""目标功能测试：SPEC-02 min_total 金额门槛筛选。

修改前必须失败（TypeError: apply_discount() got an unexpected keyword argument 'min_total'），
修改后必须全部通过。
"""

import unittest

from inventory import apply_discount, order_total


def items(total):
    """构造一个总额恰为 total 的单行订单。"""
    return [{"price": total, "qty": 1}]


class MinTotalTargetTest(unittest.TestCase):
    def test_below_threshold_returns_order_total(self):
        """SPEC-02.2：总额 150 < 门槛 200，不应用折扣，返回原价。"""
        order = items(150)
        self.assertEqual(order_total(order), 150.0)
        self.assertEqual(apply_discount(order, rate=0.9, min_total=200), 150.0)

    def test_at_threshold_applies_discount(self):
        """SPEC-02.3：总额 200 >= 门槛 200（边界包含），正常打折。"""
        self.assertEqual(apply_discount(items(200), rate=0.9, min_total=200), 180.0)

    def test_above_threshold_applies_discount(self):
        """SPEC-02.3：总额 300 > 门槛 200，正常打折。"""
        self.assertEqual(apply_discount(items(300), rate=0.8, min_total=200), 240.0)

    def test_threshold_uses_pre_discount_total(self):
        """SPEC-02.5：门槛判断使用折扣前总额，而非折扣后金额。"""
        self.assertEqual(apply_discount(items(210), rate=0.5, min_total=200), 105.0)

    def test_min_total_none_backward_compatible(self):
        """SPEC-02.1：min_total=None 与 v1 行为一致。"""
        order = items(150)
        self.assertEqual(apply_discount(order, rate=0.9), apply_discount(order, rate=0.9, min_total=None))

    def test_non_positive_min_total_raises(self):
        """SPEC-02.4：min_total <= 0 抛出 ValueError。"""
        with self.assertRaises(ValueError):
            apply_discount(items(300), rate=0.9, min_total=0)
        with self.assertRaises(ValueError):
            apply_discount(items(300), rate=0.9, min_total=-1)


if __name__ == "__main__":
    unittest.main()
