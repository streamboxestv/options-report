import unittest
from datetime import date
from unittest.mock import patch

import alpaca_options_report as report


class CreditSpreadExpirationTests(unittest.TestCase):
    def test_selects_listed_thursday_when_christmas_friday_has_no_chain(self):
        with patch.object(report, "api_get", side_effect=self.listed_chain):
            expiration = report.nearest_credit_spread_expiration(
                date(2026, 11, 24), "key", "secret"
            )
        self.assertEqual(expiration, date(2026, 12, 24))

    def test_keeps_friday_when_it_has_a_listed_chain(self):
        with patch.object(report, "api_get", side_effect=self.listed_chain):
            expiration = report.nearest_credit_spread_expiration(
                date(2026, 11, 20), "key", "secret"
            )
        self.assertEqual(expiration, date(2026, 12, 18))

    @staticmethod
    def listed_chain(path, api_key, api_secret, params):
        expirations = {
            "2026-12-18": "AAPL261218C00350000",
            "2026-12-24": "AAPL261224C00350000",
        }
        contract = expirations.get(params["expiration_date"])
        return {"snapshots": {contract: {}} if contract else {}}
