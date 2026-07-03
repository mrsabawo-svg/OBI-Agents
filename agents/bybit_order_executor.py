import time
import uuid
import random
import logging
from typing import Dict, Any, Optional, List

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OBI.ExecutionAgent")

# --- Custom Exceptions ---
class CircuitBreakerTrippedError(Exception):
    """Raised when the execution circuit breaker is open."""
    pass


class BybitOrderExecutor:
    # Error classification profiles
    FATAL_CODES = {10001, 10003, 110004, 110007, 110043, 110012}
    RATE_LIMIT_CODES = {10006, 33004}

    def __init__(
        self,
        client: Any,
        max_retries: int = 3,
        base_delay: float = 0.1,
        cb_threshold: int = 5,
        cb_window: float = 30.0
    ):
        """
        Initialize the safe Bybit Order Executor.

        :param client: SDK Client instance exposing place_order and get_open_orders
        :param max_retries: Number of retries for transient/network errors
        :param base_delay: Base delay in seconds for backoff logic
        :param cb_threshold: Max failures within cb_window to trip Circuit Breaker
        :param cb_window: Rolling window in seconds for tracking system-wide failures
        """
        self.client = client
        self.max_retries = max_retries
        self.base_delay = base_delay

        # Circuit Breaker attributes
        self.cb_threshold = cb_threshold
        self.cb_window = cb_window
        self.failure_timestamps: List[float] = []
        self.circuit_tripped = False

    def check_circuit_breaker(self) -> None:
        """Evaluates whether the circuit breaker should trigger or remain tripped."""
        now = time.time()
        # Clean up failures outside the sliding window
        self.failure_timestamps = [t for t in self.failure_timestamps if now - t <= self.cb_window]

        if len(self.failure_timestamps) >= self.cb_threshold:
            self.circuit_tripped = True
            logger.critical("Circuit Breaker is TRIPPED! Unhealthy execution path detected.")
            raise CircuitBreakerTrippedError("Circuit Breaker is OPEN. Halting execution.")

        self.circuit_tripped = False

    def record_failure(self) -> None:
        """Records a connection/system failure to the sliding window tracker."""
        self.failure_timestamps.append(time.time())
        # Re-evaluate
        try:
            self.check_circuit_breaker()
        except CircuitBreakerTrippedError:
            pass

    def place_order_safe(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Safely attempts to place an order using idempotent tracking and exponential backoff.
        """
        self.check_circuit_breaker()

        # Enforce local unique ID for safe recovery
        if "orderClientId" not in order_params:
            order_params["orderClientId"] = f"obi-{uuid.uuid4().hex[:16]}"

        cl_order_id = order_params["orderClientId"]
        category = order_params.get("category", "linear")
        attempt = 0

        while attempt < self.max_retries:
            try:
                # Execution Attempt
                response = self.client.place_order(**order_params)
                ret_code = response.get("retCode")

                # Success
                if ret_code == 0:
                    return {"status": "SUCCESS", "data": response.get("result", {})}

                # 1. Handle Rate Limits
                if ret_code in self.RATE_LIMIT_CODES:
                    attempt += 1
                    sleep_time = self._calculate_backoff(attempt, is_rate_limit=True)
                    logger.warning(f"Rate limited (Code {ret_code}). Retrying in {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
                    continue

                # 2. Handle Fatal Errors (Insufficient margin, bad parameters, invalid auth)
                if ret_code in self.FATAL_CODES:
                    logger.error(f"Fatal error encountered: {response.get('retMsg')} (Code: {ret_code}). Aborting execution.")
                    return {"status": "FAILED", "reason": "FATAL_API_ERROR", "code": ret_code}

                # 3. Handle Transient/Unclassified Errors
                logger.warning(f"Transient error code {ret_code}: {response.get('retMsg')}. Retrying...")
                attempt += 1
                time.sleep(self._calculate_backoff(attempt))

            except Exception as e:
                # Handle Network Failures / Socket Timeouts
                logger.warning(f"Network exception on attempt {attempt + 1}: {str(e)}")
                self.record_failure()
                attempt += 1

                # Query Bybit to check if the order landed during the network drop
                if self._order_exists_on_exchange(cl_order_id, category):
                    logger.info(f"Order {cl_order_id} verified on-chain despite network drop. Safe recovery executed.")
                    existing_order = self._fetch_order_by_client_id(cl_order_id, category)
                    return {"status": "SUCCESS", "data": existing_order}

                if attempt >= self.max_retries:
                    break

                sleep_time = self._calculate_backoff(attempt)
                time.sleep(sleep_time)

        logger.critical(f"Order placement failed after {self.max_retries} attempts.")
        return {"status": "FAILED", "reason": "MAX_RETRIES_EXCEEDED"}

    def _calculate_backoff(self, attempt: int, is_rate_limit: bool = False) -> float:
        """Exponential Backoff with Jitter to mitigate thundering herd issues."""
        factor = 4.0 if is_rate_limit else 2.0
        temp = self.base_delay * (factor ** attempt)
        # Apply Jitter
        sleep_time = random.uniform(self.base_delay, temp)
        return min(sleep_time, 5.0)  # Cap delay to 5 seconds to preserve execution loop

    def _order_exists_on_exchange(self, cl_order_id: str, category: str) -> bool:
        """Deduplication lookup helper."""
        try:
            res = self.client.get_open_orders(category=category, orderClientId=cl_order_id)
            if res.get("retCode") == 0 and len(res.get("result", {}).get("list", [])) > 0:
                return True
        except Exception:
            pass  # Suppress internal verification lookups
        return False

    def _fetch_order_by_client_id(self, cl_order_id: str, category: str) -> Dict[str, Any]:
        """Fetches active matching order details directly from exchange."""
        res = self.client.get_open_orders(category=category, orderClientId=cl_order_id)
        return res["result"]["list"][0]


# =====================================================================
#                      Mock Test Cases Area
# =====================================================================
import unittest
from unittest.mock import MagicMock, patch

class TestBybitOrderExecutor(unittest.TestCase):

    def setUp(self):
        self.mock_client = MagicMock()
        self.executor = BybitOrderExecutor(
            client=self.mock_client,
            max_retries=3,
            base_delay=0.001,  # Fast execution for tests
            cb_threshold=3,
            cb_window=5.0
        )

    def test_successful_order_placement(self):
        """Should succeed on standard response (retCode=0)."""
        self.mock_client.place_order.return_value = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"orderId": "12345678", "orderClientId": "obi-test"}
        }

        res = self.executor.place_order_safe({"symbol": "BTCUSDT", "side": "Buy"})
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["data"]["orderId"], "12345678")

    def test_fatal_error_aborts_immediately(self):
        """Should immediately stop and return failure upon encountering Insufficient Balance (110004)."""
        self.mock_client.place_order.return_value = {
            "retCode": 110004,
            "retMsg": "Insufficient wallet balance",
            "result": {}
        }

        res = self.executor.place_order_safe({"symbol": "BTCUSDT", "side": "Buy"})
        self.assertEqual(res["status"], "FAILED")
        self.assertEqual(res["reason"], "FATAL_API_ERROR")
        self.assertEqual(res["code"], 110004)
        # Verify it only called once
        self.assertEqual(self.mock_client.place_order.call_count, 1)

    def test_rate_limiting_backoff_recovery(self):
        """Should back off and retry successfully if initially rate limited."""
        self.mock_client.place_order.side_effect = [
            {"retCode": 10006, "retMsg": "Too many requests", "result": {}},
            {"retCode": 0, "retMsg": "OK", "result": {"orderId": "retry-success"}}
        ]

        res = self.executor.place_order_safe({"symbol": "BTCUSDT", "side": "Buy"})
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["data"]["orderId"], "retry-success")
        self.assertEqual(self.mock_client.place_order.call_count, 2)

    def test_network_timeout_with_idempotency_recovery(self):
        """Should recover and adopt order state if network drops but order registered on exchange."""
        # Setup place_order to raise Timeout exception
        self.mock_client.place_order.side_effect = ConnectionError("Connection lost.")

        # Setup query lookup to find order matching client order ID
        self.mock_client.get_open_orders.return_value = {
            "retCode": 0,
            "result": {
                "list": [{"orderId": "recovered-id", "orderStatus": "New"}]
            }
        }

        res = self.executor.place_order_safe({"symbol": "BTCUSDT", "side": "Buy"})
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["data"]["orderId"], "recovered-id")
        self.mock_client.get_open_orders.assert_called()

    def test_circuit_breaker_tripping(self):
        """Circuit breaker should trip when consecutive failures exceed threshold."""
        self.mock_client.place_order.side_effect = ConnectionError("Host unreachable.")
        self.mock_client.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}

        # Exceed threshold (threshold is set to 3)
        with self.assertRaises(CircuitBreakerTrippedError):
            for _ in range(4):
                self.executor.place_order_safe({"symbol": "BTCUSDT", "side": "Buy"})


if __name__ == "__main__":
    unittest.main()
