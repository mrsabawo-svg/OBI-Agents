import unittest
from unittest.mock import MagicMock, patch

import main


class Pass12OrchestrationTest(unittest.TestCase):
    def _run(self, intelligence_result):
        data_agent = MagicMock()
        data_agent.fetch.return_value = {"4h": [1]}
        data_agent.ticker = "TEST"

        session = {"tradeable": True, "reason": "", "kill_zone": False}
        bias = MagicMock(approved=True)
        trigger = MagicMock(fire=True, reason="", grade="A")

        with patch.object(main, "sast_str", return_value="2026-09-24 07:00:00"), \
             patch.object(main.DataAgent, return_value=data_agent), \
             patch.object(main, "check_outcome"), \
             patch.object(main.SessionAgent, return_value=MagicMock(analyse=MagicMock(return_value=session))), \
             patch.object(main.HTFAgent, return_value=MagicMock(analyse=MagicMock(return_value={}))), \
             patch.object(main.RegimeAgent, return_value=MagicMock(detect=MagicMock(return_value={"label": "TRENDING"}))), \
             patch.object(main.MTFAgent, return_value=MagicMock(analyse=MagicMock(return_value={}))), \
             patch.object(main.BiasAgent, return_value=MagicMock(evaluate=MagicMock(return_value=bias))), \
             patch.object(main.ZoneAgent, return_value=MagicMock(analyse=MagicMock(return_value={}))), \
             patch.object(main.LTFAgent, return_value=MagicMock(analyse=MagicMock(return_value={}))), \
             patch.object(main.TriggerAgent, return_value=MagicMock(evaluate=MagicMock(return_value=trigger))), \
             patch.object(main.EdgeAgent, return_value=MagicMock(analyse=MagicMock(return_value=MagicMock()))), \
             patch.object(main.ScoreAgent, return_value=MagicMock(compute=MagicMock(return_value=MagicMock()))), \
             patch.object(main.ExecutionAgent, return_value=MagicMock(propose=MagicMock())), \
             patch.object(main.IntelligenceAgent, return_value=MagicMock(verdict=MagicMock(return_value=intelligence_result))) as intelligence_cls, \
             patch.object(main.ArchiveAgent, return_value=MagicMock()) as archive_cls:

            result = main.run("XAUUSD", {"safe": True})

        return result, intelligence_cls.return_value, archive_cls.return_value

    def test_duplicate_intelligence_result_skips_archive(self):
        result, intelligence, archive = self._run({})

        self.assertEqual(result, {"fired": True})
        intelligence.verdict.assert_called_once()
        archive.log.assert_not_called()

    def test_accepted_intelligence_result_allows_archive(self):
        result, intelligence, archive = self._run({"id": "XAUUSD_TEST"})

        self.assertEqual(result, {"fired": True})
        intelligence.verdict.assert_called_once()
        archive.log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
