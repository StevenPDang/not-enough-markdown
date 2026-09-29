import tempfile
import unittest
from pathlib import Path
import sys
import subprocess
import io
from contextlib import redirect_stdout
from unittest.mock import patch
import json
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from urag.search import open_index, refresh_index, search
from urag.cli import main


class TtyBuffer(io.StringIO):
    def isatty(self):
        return True


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.db = open_index(Path(self.temp.name) / "index.sqlite3")
        self.addCleanup(self.db.close)

    def test_finds_section_and_line_for_natural_query(self):
        (self.root / "BUILD.md").write_text(
            "# Build\n\n## Compiler settings\nUse `make MODE=fast` to compile with fast settings.\n"
            "\n## Tests\nRun pytest.\n", encoding="utf-8"
        )
        refresh_index(self.db, self.root)
        hits = search(self.db, "How to compile with fast settings")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["path"], "BUILD.md")
        self.assertEqual(hits[0]["heading"], "Compiler settings")
        self.assertEqual(hits[0]["line"], 4)

    def test_refresh_updates_and_removes_results(self):
        doc = self.root / "guide.md"
        doc.write_text("# Deploy\nRun the moonship command.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        self.assertTrue(search(self.db, "moonship"))
        doc.write_text("# Deploy\nRun the starship command.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        self.assertFalse(search(self.db, "moonship"))
        self.assertTrue(search(self.db, "starship"))
        doc.unlink()
        refresh_index(self.db, self.root)
        self.assertFalse(search(self.db, "starship"))

    def test_recovers_typo(self):
        (self.root / "guide.md").write_text(
            "# Compilation\nConfigure compilation flags here.\n", encoding="utf-8"
        )
        (self.root / "other.md").write_text("# Flags\nFlags for deployment.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        self.assertEqual(search(self.db, "compilaton")[0]["path"], "guide.md")
        self.assertEqual(search(self.db, "compilaton flags")[0]["path"], "guide.md")

    def test_ranks_multiple_query_concepts_above_one(self):
        (self.root / "one.md").write_text("# Compile\nCompile compile compile.\n", encoding="utf-8")
        (self.root / "two.md").write_text("# Build settings\nUse the fast build setting.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        self.assertEqual(search(self.db, "compile settings")[0]["path"], "two.md")

    def test_git_ignored_docs_are_excluded(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("private.md\n", encoding="utf-8")
        (self.root / "private.md").write_text("# Secret\nHidden instructions.\n", encoding="utf-8")
        (self.root / "public.md").write_text("# Public\nVisible instructions.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        self.assertFalse(search(self.db, "hidden"))
        self.assertTrue(search(self.db, "visible"))

    def test_fetch_command_returns_matching_passage(self):
        (self.root / "install.md").write_text("# Install\nRun the setup command.\n", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["fetch", "setup", "--root", str(self.root), "--index", str(Path(self.temp.name) / "cli.sqlite3")])
        self.assertEqual(status, 0)
        self.assertIn("install.md:2 [Install]", output.getvalue())

    def test_fetch_styles_tty_but_respects_no_color(self):
        (self.root / "install.md").write_text("# Install\nRun the setup command.\n", encoding="utf-8")
        args = ["fetch", "setup", "--root", str(self.root), "--index", str(Path(self.temp.name) / "cli.sqlite3")]
        styled = TtyBuffer()
        with patch.dict(os.environ, {"NO_COLOR": ""}), redirect_stdout(styled):
            main(args)
        self.assertIn("\x1b[1;36minstall.md:2\x1b[0m", styled.getvalue())
        plain = TtyBuffer()
        with patch.dict(os.environ, {"NO_COLOR": "1"}), redirect_stdout(plain):
            main(args)
        self.assertNotIn("\x1b[", plain.getvalue())

    def test_json_remains_machine_readable_on_tty(self):
        (self.root / "install.md").write_text("# Install\nRun the setup command.\n", encoding="utf-8")
        output = TtyBuffer()
        with redirect_stdout(output):
            main(["fetch", "setup", "--json", "--root", str(self.root), "--index", str(Path(self.temp.name) / "cli.sqlite3")])
        self.assertEqual(json.loads(output.getvalue())[0]["path"], "install.md")


if __name__ == "__main__":
    unittest.main()
