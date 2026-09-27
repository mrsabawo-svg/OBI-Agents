"""Canonical market-data identity and contract for OBI.

Logical symbols are strategy identities. Source tickers are explicit data
instruments; they must not be silently substituted across generation and
lifecycle evaluation.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class MarketDataContract:
    symbol: str
    source: str
    instrument_type: str
    venue: str
    session_model: str


CONTRACTS: Dict[str, MarketDataContract] = {
    "XAUUSD": MarketDataContract("XAUUSD", "GC=F", "COMEX gold futures", "CME/COMEX", "near_24h_futures"),
    "EURUSD": MarketDataContract("EURUSD", "EURUSD=X", "spot FX", "Yahoo Finance", "FX"),
    "USDJPY": MarketDataContract("USDJPY", "USDJPY=X", "spot FX", "Yahoo Finance", "FX"),
    "GBPJPY": MarketDataContract("GBPJPY", "GBPJPY=X", "spot FX", "Yahoo Finance", "FX"),
    "GBPUSD": MarketDataContract("GBPUSD", "GBPUSD=X", "spot FX", "Yahoo Finance", "FX"),
    "BTCUSD": MarketDataContract("BTCUSD", "BTC-USD", "spot crypto", "Yahoo Finance", "24x7"),
    "ETHUSD": MarketDataContract("ETHUSD", "ETH-USD", "spot crypto", "Yahoo Finance", "24x7"),
    "SOLUSD": MarketDataContract("SOLUSD", "SOL-USD", "spot crypto", "Yahoo Finance", "24x7"),
    "NASDAQ": MarketDataContract("NASDAQ", "QQQ", "equity ETF", "NASDAQ", "US_equities"),
}


def get_contract(symbol: str) -> MarketDataContract:
    try:
        return CONTRACTS[symbol]
    except KeyError as exc:
        raise ValueError("No market-data contract for " + str(symbol)) from exc
