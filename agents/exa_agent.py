"""
OBI Agents — Exa Agent
Fetches real-time market news and sentiment for each symbol.
Feeds context into Intelligence Agent before Groq analysis.
"""
import os
import requests

EXA_API_KEY = os.environ.get("EXA_API_KEY")

SYMBOL_SEARCH_TERMS = {
    "XAUUSD": "gold price forecast today XAU USD",
    "EURUSD": "EUR USD euro dollar forecast today",
    "USDJPY": "USD JPY dollar yen forecast today",
    "GBPJPY": "GBP JPY pound yen forecast today",
    "GBPUSD": "GBP USD pound dollar forecast today",
    "BTCUSD": "Bitcoin BTC price analysis today",
    "ETHUSD": "Ethereum ETH price analysis today",
    "SOLUSD": "Solana SOL price analysis today",
    "NASDAQ": "NASDAQ QQQ market analysis today",
}


class ExaAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def get_context(self) -> str:
        print("[EXA] Fetching news for " + self.symbol)
        try:
            query = SYMBOL_SEARCH_TERMS.get(self.symbol, self.symbol + " market analysis today")
            r = requests.post(
                "https://api.exa.ai/search",
                headers={
                    "x-api-key": str(EXA_API_KEY),
                    "Content-Type": "application/json"
                },
                json={
                    "query":          query,
                    "numResults":     3,
                    "type":           "neural",
                    "useAutoprompt":  True,
                    "contents": {
                        "text":      {"maxCharacters": 300},
                        "highlights": {"numSentences": 2}
                    }
                },
                timeout=15
            )

            if r.status_code != 200:
                print("[EXA] Status: " + str(r.status_code))
                return "No market context available"

            results = r.json().get("results", [])
            if not results:
                return "No recent news found"

            snippets = []
            for res in results[:3]:
                title   = res.get("title", "")
                text    = res.get("text", "")[:200]
                if title:
                    snippets.append(title + ": " + text)

            context = " | ".join(snippets)
            print("[EXA] Context fetched for " + self.symbol)
            return context[:600]

        except Exception as e:
            print("[EXA] Error: " + str(e))
            return "Market context unavailable"
