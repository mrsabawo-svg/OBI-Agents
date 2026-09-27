import unittest

from core.market_data_contract import CONTRACTS, get_contract
from agents.data_agent import DataAgent
from agents.lifecycle_agent import SYMBOL_MAP as LIFECYCLE_SYMBOL_MAP


class MarketDataContractTest(unittest.TestCase):
    def test_all_production_symbols_have_explicit_contracts(self):
        expected = {
            "XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "GBPUSD",
            "BTCUSD", "ETHUSD", "SOLUSD", "NASDAQ",
        }
        self.assertEqual(set(CONTRACTS), expected)

    def test_xauusd_is_not_mapped_to_gld(self):
        contract = get_contract("XAUUSD")
        self.assertEqual(contract.source, "GC=F")
        self.assertNotEqual(contract.source, "GLD")
        self.assertEqual(contract.instrument_type, "COMEX gold futures")

    def test_generation_and_lifecycle_share_source_identity(self):
        for symbol, contract in CONTRACTS.items():
            self.assertEqual(DataAgent(symbol).ticker, contract.source)
            self.assertEqual(LIFECYCLE_SYMBOL_MAP[symbol], contract.source)

    def test_unknown_symbol_is_rejected(self):
        with self.assertRaises(ValueError):
            get_contract("NOT_A_REAL_SYMBOL")


if __name__ == "__main__":
    unittest.main()
