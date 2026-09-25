import unittest
from unittest.mock import patch, MagicMock

from core import memory


class MemoryTransactionTest(unittest.TestCase):
    def test_save_increments_revision_and_records_transaction(self):
        remote = {"XAUUSD": {"signals": 1}}
        response = MagicMock(status_code=200)

        with patch("core.memory._get_remote", side_effect=[
            remote,
            {
                "XAUUSD": {"signals": 1},
                "_memory_meta": {"revision": 1, "transactions": ["tx-1"]},
            },
        ]), patch("core.memory.requests.patch", return_value=response) as patch_call:
            data = dict(remote)
            self.assertTrue(memory.save(data, expected_revision=0, transaction_id="tx-1"))

        payload = patch_call.call_args.kwargs["json"]
        saved = payload["files"]["obi_memory.json"]["content"]
        self.assertIn('"revision": 1', saved)
        self.assertIn('"tx-1"', saved)

    def test_stale_revision_is_rejected_before_patch(self):
        remote = {"_memory_meta": {"revision": 9}}
        with patch("core.memory._get_remote", return_value=remote),              patch("core.memory.requests.patch") as patch_call:
            with self.assertRaisesRegex(RuntimeError, "MEMORY_CONFLICT"):
                memory.save({}, expected_revision=8, transaction_id="tx-stale")
            patch_call.assert_not_called()

    def test_duplicate_transaction_is_idempotent(self):
        remote = {
            "_memory_meta": {
                "revision": 12,
                "transactions": ["tx-existing"],
            }
        }
        with patch("core.memory._get_remote", return_value=remote),              patch("core.memory.requests.patch") as patch_call:
            result = memory.transaction(
                lambda m: m.update({"should_not_change": True}),
                transaction_id="tx-existing",
            )
            self.assertNotIn("should_not_change", result)
            patch_call.assert_not_called()

    def test_transaction_mutates_latest_snapshot(self):
        remote = {
            "XAUUSD": {"signals": 3},
            "_memory_meta": {"revision": 20, "transactions": []},
        }
        persisted = {
            "XAUUSD": {"signals": 4},
            "_memory_meta": {"revision": 21, "transactions": ["tx-21"]},
        }
        with patch("core.memory._get_remote", side_effect=[remote, persisted]),              patch("core.memory.requests.patch", return_value=MagicMock(status_code=200)):
            result = memory.transaction(
                lambda m: m["XAUUSD"].update({"signals": 4}),
                transaction_id="tx-21",
            )
        self.assertEqual(result["XAUUSD"]["signals"], 4)
        self.assertEqual(result["_memory_meta"]["revision"], 21)


if __name__ == "__main__":
    unittest.main()
