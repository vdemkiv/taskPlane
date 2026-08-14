"""Turn a basket into a total, in cents."""

from .catalog import price_of
from .discount import apply

TAX_RATE = 0.2


def subtotal(basket):
    """What the basket costs before tax."""
    return sum(price_of(sku) * int(qty) for sku, qty in basket)


def total(basket, code=None):
    """What the basket costs with any discount applied, plus tax."""
    amount = subtotal(basket)
    if code:
        amount = apply(amount, code)
    return round(amount * (1 + TAX_RATE))
