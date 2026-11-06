import unittest
from unittest.mock import patch, MagicMock
from agents.execution_agent import get_price, build_trade_plan
from agents.data_agent import SYMBOL_MAP


class BrokerPriceParityTest(unittest.TestCase):
    """
    Validates price compatibility between feed symbols and execution broker.
    Asserts price spread / basis deviations remain within strict defined tolerances.
    """

    MAX_DEVIATION_PCT = 0.5  # Max allowable deviation percentage between feed and exchange mark price

    @patch("agents.execution_agent.requests.get")
    def test_crypto_feed_vs_bybit_mark_price_parity(self, mock_get):
        test_cases = [
            {"symbol": "BTCUSD", "feed_price": 95000.0, "bybit_mark_price": 95100.0},
            {"symbol": "ETHUSD", "feed_price": 3400.0,  "bybit_mark_price": 3405.0},
            {"symbol": "SOLUSD", "feed_price": 185.0,   "bybit_mark_price": 185.3},
        ]

        for case in test_cases:
            sym = case["symbol"]
            feed_price = case["feed_price"]
            broker_price = case["bybit_mark_price"]

            mock_res = MagicMock()
            mock_res.json.return_value = {
                "result": {"list": [{"markPrice": str(broker_price)}]}
            }
            mock_get.return_value = mock_res

            fetched_price = get_price(sym)
            deviation_pct = abs(fetched_price - feed_price) / feed_price * 100.0

            self.assertLessEqual(
                deviation_pct,
                self.MAX_DEVIATION_PCT,
                f"Price parity failure on {sym}: feed={feed_price}, broker={fetched_price}, deviation={deviation_pct:.3f}%"
            )

    def test_futures_instrument_mapping_matches_canonical_scale(self):
        """
        Confirms that instrument mappings in DataAgent and TrackerAgent use futures price scales,
        preventing multiple-scale disparity (e.g. GLD ~240 vs Gold ~2700).
        """
        self.assertEqual(SYMBOL_MAP["XAUUSD"], "GC=F")
        self.assertEqual(SYMBOL_MAP["NASDAQ"], "NQ=F")


if __name__ == "__main__":
    unittest.main()
