import unittest
from datetime import date, datetime, timezone
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


class CashSecuredPutRoiTests(unittest.TestCase):
    def test_put_with_positive_premium_is_sell_below_one_percent_roi(self):
        self.assertEqual(report.action_for_option(0.50, 100.0, "put"), "Sell")

    def test_covered_call_keeps_one_percent_minimum(self):
        self.assertEqual(report.action_for_option(0.50, 100.0, "call"), "Wait")
        self.assertEqual(report.action_for_option(1.00, 100.0, "call"), "Sell")

    def test_put_without_positive_premium_still_waits(self):
        self.assertEqual(report.action_for_option(None, 100.0, "put"), "Wait")
        self.assertEqual(report.action_for_option(0.0, 100.0, "put"), "Wait")


class CashSecuredPutLiquidityTests(unittest.TestCase):
    def snapshot(self, strike, bid, ask, bid_size, open_interest, last):
        return {
            "option_contract": {
                "strike_price": str(strike),
                "open_interest": str(open_interest),
            },
            "greeks": {"delta": -0.20},
            "latestQuote": {"bp": bid, "ap": ask, "bs": bid_size},
            "latestTrade": {"p": last},
        }

    def test_skips_stale_last_trade_when_there_is_no_bid(self):
        strike, premium = report.choose_option_contract(
            contracts=[
                self.snapshot(57, None, 0.60, 0, 100, 0.88),
                self.snapshot(55, 0.50, 0.60, 10, 100, 0.70),
            ],
            price=65.98,
            target_strike=57.0,
            option_type="put",
            cash_budget=20_000.0,
        )

        self.assertEqual(strike, 55)
        self.assertEqual(premium, 0.50)

    def test_rejects_contract_without_enough_open_interest(self):
        with self.assertRaisesRegex(RuntimeError, "No OTM put contracts available"):
            report.choose_option_contract(
                contracts=[
                    self.snapshot(57, 0.50, 0.60, 10, 20, 0.55),
                ],
                price=65.98,
                target_strike=57.0,
                option_type="put",
                cash_budget=20_000.0,
            )


class CronOptionSelectorCompatibilityTests(unittest.TestCase):
    def test_cash_budget_is_forwarded_to_liquidity_aware_selector(self):
        from api.cron import refresh

        contracts = [
            {
                "option_contract": {"strike_price": "55", "open_interest": "100"},
                "greeks": {"delta": -0.20},
                "latestQuote": {"bp": 0.50, "ap": 0.60, "bs": 10},
                "latestTrade": {"p": 0.70},
            }
        ]
        strike, premium = refresh.choose_priced_option_contract(
            contracts,
            price=65.98,
            target_strike=55.0,
            option_type="put",
            cash_budget=20_000.0,
        )

        self.assertEqual(strike, 55)
        self.assertEqual(premium, 0.50)


class CronScheduleRetryTests(unittest.TestCase):
    def test_runs_at_nine_and_ten_chicago_time(self):
        from api.cron import refresh

        self.assertTrue(refresh.should_run_refresh(datetime(2026, 10, 28, 14, tzinfo=timezone.utc)))
        self.assertTrue(refresh.should_run_refresh(datetime(2026, 10, 28, 15, tzinfo=timezone.utc)))
        self.assertFalse(refresh.should_run_refresh(datetime(2026, 10, 28, 16, tzinfo=timezone.utc)))

    def test_retry_skips_only_a_valid_current_report(self):
        from api.cron import refresh

        report_date = date(2026, 10, 28)
        self.assertTrue(
            refresh.latest_report_is_current(
                '{"reportDateIso":"2026-10-28","includedCount":150}',
                report_date,
            )
        )
        self.assertFalse(
            refresh.latest_report_is_current(
                '{"reportDateIso":"2026-10-28","includedCount":0}',
                report_date,
            )
        )
        self.assertFalse(
            refresh.latest_report_is_current(
                '{"reportDateIso":"2026-10-27","includedCount":150}',
                report_date,
            )
        )


class CashPutSupplementMergeTests(unittest.TestCase):
    def test_merges_extra_rows_without_replacing_core_report(self):
        from api.cron import refresh

        base = {
            "reportDateIso": "2026-10-28",
            "includedCount": 150,
            "warnings": ["core warning"],
            "cashSecuredPuts": {
                "rows": [
                    {"ticker": "AAPL", "roiPct": 1.0, "premium": 100.0},
                ]
            },
        }
        supplement = {
            "pricingRefreshedAt": "2026-10-28 10:05:00",
            "warnings": ["extra warning"],
            "cashSecuredPuts": {
                "rows": [
                    {"ticker": "MARA", "roiPct": 2.0, "premium": 80.0},
                ]
            },
        }

        merged = refresh.merge_cash_put_supplement(base, supplement)

        self.assertEqual(merged["reportDateIso"], "2026-10-28")
        self.assertEqual(merged["includedCount"], 150)
        self.assertEqual(
            [row["ticker"] for row in merged["cashSecuredPuts"]["rows"]],
            ["MARA", "AAPL"],
        )
        self.assertEqual(merged["cashPutPricingRefreshedAt"], "2026-10-28 10:05:00")
        self.assertEqual(merged["warnings"], ["core warning", "extra warning"])


class CashSecuredPutUniverseTests(unittest.TestCase):
    def test_extra_universe_contains_100_unique_non_primary_symbols(self):
        extras = report.cash_secured_put_extra_symbols(report.OPTIONS_REPORT_STOCKS)

        self.assertEqual(len(extras), 100)
        self.assertEqual(len(extras), len(set(extras)))
        self.assertTrue(set(extras).isdisjoint(report.OPTIONS_REPORT_STOCKS))

    def test_primary_symbol_is_removed_from_extra_scan(self):
        extras = report.cash_secured_put_extra_symbols(["MARA"])

        self.assertNotIn("MARA", extras)
        self.assertIn("NOK", extras)


if __name__ == "__main__":
    unittest.main()
