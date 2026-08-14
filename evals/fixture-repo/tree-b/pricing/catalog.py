"""The catalog: what one unit of a SKU costs, in cents."""

CATALOG = {
    "widget": 1250,
    "gizmo": 899,
    "sprocket": 45,
}


def price_of(sku):
    """The unit price of `sku`, in cents.

    An unknown SKU raises rather than returning zero: a silent zero prices
    a typo as a free item, and nothing downstream can tell the difference.
    """
    return CATALOG[sku]
