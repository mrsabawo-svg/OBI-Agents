import unittest
from pathlib import Path


class Pass15MemoryConcurrencyTest(unittest.TestCase):
    BRANCH_WORKFLOWS = (
        ".github/workflows/obi_pipeline.yml",
        ".github/workflows/telegram_commands.yml",
    )

    def test_all_gist_writers_share_one_concurrency_group(self):
        for path in self.BRANCH_WORKFLOWS:
            text = Path(path).read_text()
            self.assertIn("concurrency:", text, path)
            self.assertIn("group: obi-gist-writer", text, path)
            self.assertIn("cancel-in-progress: false", text, path)

    def test_pipeline_and_telegram_use_same_writer_group(self):
        texts = [Path(path).read_text() for path in self.BRANCH_WORKFLOWS]
        groups = []
        for text in texts:
            line = next(line.strip() for line in text.splitlines()
                         if line.strip().startswith("group:"))
            groups.append(line)
        self.assertEqual(groups[0], groups[1])


if __name__ == "__main__":
    unittest.main()
