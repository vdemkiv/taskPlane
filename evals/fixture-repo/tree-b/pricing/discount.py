"""Order-level discounts, as a percentage off the subtotal."""

CODES = {
    "WELCOME": 10,
    "LOYALTY": 15,
    "STAFF": 50,
}


def percent_off(code):
    """The percentage `code` takes off. An unknown code takes nothing."""
    return CODES.get(code, 0)


def apply(amount, code):
    """`amount` in cents, with `code` applied."""
    return round(amount * (100 - percent_off(code)) / 100)
