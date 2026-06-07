"""
OBI Agents — HMM Regime Agent
Detects market regime using Hidden Markov Model.
Replaces simple EMA bias with statistical regime classification.
States: 0=RANGING, 1=TRENDING, 2=VOLATILE
"""
import numpy as np
from hmmlearn.hmm import GaussianHMM

REGIME_LABELS = {0: "RANGING", 1: "TRENDING", 2: "VOLATILE"}

class RegimeAgent:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def detect(self, market_data: dict) -> dict:
        df = None
        if "1h" in market_data and not market_data["1h"].empty:
            df = market_data["1h"]
        elif "4h" in market_data and not market_data["4h"].empty:
            df = market_data["4h"]

        if df is None or len(df) < 50:
            return self._default()

        try:
            features = self._build_features(df)
            if features is None or len(features) < 30:
                return self._default()

            result = self._run_hmm(features)
            print("[REGIME] " + self.symbol + ": " + result["label"] + " (conf=" + str(round(result["confidence"], 2)) + ")")
            return result

        except Exception as e:
            print("[REGIME] " + self.symbol + " error: " + str(e))
            return self._default()

    def _build_features(self, df):
        try:
            close   = df["Close"].squeeze().values.flatten()
            high    = df["High"].squeeze().values.flatten()
            low     = df["Low"].squeeze().values.flatten()
            returns = np.diff(np.log(close + 1e-9))
            n       = len(returns)

            # Volatility
            vol = np.array([np.std(returns[max(0,i-5):i+1]) for i in range(n)])

            # Range ratio
            hl_range = (high[1:] - low[1:]) / (close[1:] + 1e-9)

            # Momentum
            momentum = np.array([
                np.mean(returns[max(0,i-3):i+1]) for i in range(n)
            ])

            features = np.column_stack([returns, vol, hl_range, momentum])
            features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)
            return features

        except Exception as e:
            print("[REGIME] Feature error: " + str(e))
            return None

    def _run_hmm(self, features: np.ndarray) -> dict:
        for cov_type in ["diag", "full", "spherical"]:
            try:
                model = GaussianHMM(
                    n_components=3,
                    covariance_type=cov_type,
                    n_iter=100,
                    random_state=42,
                    verbose=False
                )
                model.fit(features)
                states     = model.predict(features)
                current    = int(states[-1])
                probs      = model.predict_proba(features)
                confidence = float(probs[-1][current])

                # Map HMM states to regime labels by volatility
                means = model.means_
                vol_idx = 0
                state_vols = [(i, float(means[i][1])) for i in range(3)]
                state_vols.sort(key=lambda x: x[1])

                state_map = {}
                state_map[state_vols[0][0]] = 0  # lowest vol = RANGING
                state_map[state_vols[1][0]] = 1  # mid vol = TRENDING
                state_map[state_vols[2][0]] = 2  # highest vol = VOLATILE

                mapped = state_map.get(current, 0)
                label  = REGIME_LABELS.get(mapped, "RANGING")

                return {
                    "state":      mapped,
                    "label":      label,
                    "confidence": confidence,
                    "raw_state":  current
                }

            except Exception as e:
                print("[REGIME] HMM (" + cov_type + ") failed: " + str(e))
                continue

        return self._default()

    def _default(self) -> dict:
        return {"state": 0, "label": "RANGING", "confidence": 0.5, "raw_state": 0}
