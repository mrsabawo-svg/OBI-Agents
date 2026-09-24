import unittest
from unittest.mock import patch, MagicMock

import main


class Pass12OrchestrationTest(unittest.TestCase):
    def _pipeline_patches(self):
        data_agent = MagicMock()
        data_agent.fetch.return_value = {"4h": [1]}
        data_agent.ticker = "TEST"

        session = {"tradeable": True, "reason": "", "kill_zone": False}
        bias = MagicMock(approved=True)
        trigger = MagicMock(fire=True, reason="", grade="A")
        edge = MagicMock()
        score = MagicMock()

        return [
            patch.object(main.DataAgent, return_value=data_agent),
            patch.object(main.check_outcome),
            patch.object(main.SessionAgent, return_value=MagicMock(analyse=MagicMock(return_value=session))),
            patch.object(main.HTFAgent, return_value=MagicMock(analyse=MagicMock(return_value={}))),
            patch.object(main.RegimeAgent, return_value=MagicMock(detect=MagicMock(return_value={"label": "TRENDING"}))),
            patch.object(main.MTFAgent, return_value=MagicMock(analyse=MagicMock(return_value={}))),
            patch.object(main.BiasAgent, return_value=MagicMock(evaluate=MagicMock(return_value=bias))),
            patch.object(main.ZoneAgent, return_value=MagicMock(analyse=MagicMock(return_value={}))),
            patch.object(main.LTFAgent, return_value=MagicMock(analyse=MagicMock(return_value={}))),
            patch.object(main.TriggerAgent, return_value=MagicMock(evaluate=MagicMock(return_value=trigger))),
            patch.object(main.EdgeAgent, return_value=MagicMock(analyse=MagicMock(return_value=edge))),
            patch.object(main.ScoreAgent, return_value=MagicMock(compute=MagicMock(return_value=score))),
            patch.object(main.sast_str, return_value="2026-09-24 07:00:00"),
        ]

    def test_duplicate_intelligence_result_skips_archive(self):
        patches = self._pipeline_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10], patches[11], patches[12]:
            intelligence = MagicMock(verdict=MagicMock(return_value={}))
            archive = MagicMock()
            with patch.object(main.IntelligenceAgent, return_value=intelligence), patch.object(main.ArchiveAgent, return_value=archive):
                result = main.run("XAUUSD", {"safe": True})

        self.assertEqual(result, {"fired": True})
        intelligence.verdict.assert_called_once()
        archive.log.assert_not_called()

    def test_accepted_intelligence_result_allows_archive(self):
        patches = self._pipeline_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10], patches[11], patches[12]:
            intelligence = MagicMock(verdict=MagicMock(return_value={"id": "XAUUSD_TEST"}))
            archive = MagicMock()
            with patch.object(main.IntelligenceAgent, return_value=intelligence), patch.object(main.ArchiveAgent, return_value=archive):
                result = main.run("XAUUSD", {"safe": True})

        self.assertEqual(result, {"fired": True})
        intelligence.verdict.assert_called_once()
        archive.log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
