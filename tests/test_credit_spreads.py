import unittest
from datetime import date
from unittest.mock import patch

import alpaca_options_report as report


def option_snapshot(strike, delta, bid, ask, last):
    return {
        "option_contract": {
            "strike_price": str(strike),
            "open_interest": "100",
        },
        "greeks": {"delta": -delta},
        "latestQuote": {"bp": bid, "ap": ask},
        "latestTrade": {"p": last},
    }


class CreditSpreadPricingTests(unittest.TestCase):
    def choose(self, snapshots):
        with patch.object(report, "paged_option_chain", return_value=snapshots):
            return report.choose_credit_spread(
                "QQQ",
                735.0,
                "Buy",
                date(2026, 10, 23),
                "key",
                "secret",
            )

    def test_uses_short_bid_and_long_ask_instead_of_stale_last_trades(self):
        row = self.choose(
            [
                option_snapshot(700, 0.20, 2.00, 2.10, 7.50),
                option_snapshot(695, 0.10, 1.00, 1.10, 1.94),
            ]
        )

        self.assertEqual(row.status, "Qualified")
        self.assertAlmostEqual(row.credit, 90.0)
        self.assertAlmostEqual(row.max_loss, 410.0)
        self.assertAlmostEqual(row.max_roi_pct, 21.9512195122)

    def test_rejects_credit_above_twenty_five_percent_of_width(self):
        row = self.choose(
            [
                option_snapshot(703, 0.20, 5.80, 5.90, 7.50),
                option_snapshot(697, 0.10, 0.20, 0.24, 1.94),
            ]
        )

        self.assertEqual(row.status, "No Trade - No Bull Put Meets Credit/Delta")
        self.assertIsNone(row.credit)
        self.assertIsNone(row.max_roi_pct)


if __name__ == "__main__":
    unittest.main()
