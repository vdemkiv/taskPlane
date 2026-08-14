import unittest

from pricing.discount import apply, percent_off


class TestADiscountCodeTakesAPercentageOff(unittest.TestCase):

    def test_a_known_code_reports_its_percentage(self):
        self.assertEqual(percent_off("WELCOME"), 10)

    def test_an_unknown_code_takes_nothing_off(self):
        self.assertEqual(percent_off("NOPE"), 0)

    def test_applying_a_code_reduces_the_amount(self):
        self.assertEqual(apply(2500, "WELCOME"), 2250)

    def test_applying_no_code_leaves_the_amount_alone(self):
        self.assertEqual(apply(2500, None), 2500)


if __name__ == "__main__":
    unittest.main()
