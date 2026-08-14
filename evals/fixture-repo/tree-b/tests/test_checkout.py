import unittest

from pricing.checkout import subtotal, total


class TestTheBasketIsPricedFromTheCatalog(unittest.TestCase):

    def test_an_empty_basket_costs_nothing(self):
        self.assertEqual(subtotal([]), 0)

    def test_a_quantity_multiplies_the_unit_price(self):
        self.assertEqual(subtotal([("widget", 2)]), 2500)

    def test_tax_is_applied_to_the_whole_basket(self):
        self.assertEqual(total([("widget", 2)]), 3000)

    def test_a_discount_code_comes_off_before_tax(self):
        self.assertEqual(total([("widget", 2)], code="WELCOME"), 2700)


if __name__ == "__main__":
    unittest.main()
