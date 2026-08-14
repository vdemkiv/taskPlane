"""Turn a basket into a total, in cents."""

from .catalog import price_of

TAX_RATE = 0.2


def subtotal(basket):
    """What the basket costs before tax."""
    return sum(price_of(sku) * int(qty) for sku, qty in basket)


def total(basket):
    """What the basket costs with tax, rounded to the nearest cent."""
    return round(subtotal(basket) * (1 + TAX_RATE))
