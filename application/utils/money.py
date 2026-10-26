"""Dollar amounts as a person reads them."""

from decimal import Decimal


def format_money(value: Decimal) -> str:
    """``Decimal('1450')`` -> ``'$1,450'``; cents only when there are some."""
    return f'${value:,.0f}' if value == value.to_integral_value() else f'${value:,.2f}'
