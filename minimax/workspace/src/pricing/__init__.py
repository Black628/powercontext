"""Order pricing package.

Business rules live in ``specs/pricing_rules.md``; keep this package small and
readable so specification changes remain easy to trace.
"""

from pricing.engine import settle

__all__ = ["settle"]
