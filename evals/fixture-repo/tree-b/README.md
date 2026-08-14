# pricing

A tiny order-pricing library.

## Rules

* every line is priced from the catalog, in cents
* an unknown SKU is an error, never a free item
* a discount code takes a percentage off the subtotal
* tax is applied to the order total

## Discounts

    total([("widget", 2)], code="WELCOME")

Codes live in `pricing/discount.py`. An unknown code takes nothing off.
