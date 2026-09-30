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

from nemd.search import open_index, refresh_index, search
from nemd.cli import main, _markdown_kinds, _pick_result, _read_key, _view_result


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

    def test_excerpt_keeps_relevant_code_block_and_caps_prose(self):
        (self.root / "commands.md").write_text(
            "# Evaluation\nRun this command:\n\n```sh\n"
            "pixi run eval --checkpoint rt-j \\\n"
            "  --pre-dir relbench \\\n"
            "  --out-dir eval_out\n```\n", encoding="utf-8")
        (self.root / "prose.md").write_text(
            "# Long explanation\n" + "\n".join("Context line %d" % n for n in range(12))
            + "\nThe answer is here.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        command = search(self.db, "eval checkpoint")[0]
        self.assertEqual(command["line"], 5)
        self.assertEqual(command["excerpt"].count("\n"), 2)
        self.assertIn("--out-dir eval_out", command["excerpt"])
        output = io.StringIO()
        with redirect_stdout(output):
            main(["search", "eval checkpoint", "--root", str(self.root),
                  "--index", str(Path(self.temp.name) / "snippet.sqlite3")])
        self.assertIn("\n    --pre-dir relbench", output.getvalue())
        prose = next(hit for hit in search(self.db, "answer", 10) if hit["path"] == "prose.md")
        self.assertLessEqual(len(prose["excerpt"].splitlines()), 8)
        self.assertIn("The answer is here.", prose["excerpt"])

    def test_long_code_block_shows_matching_command_stanza(self):
        (self.root / "commands.md").write_text(
            "# Commands\n```sh\n" + "\n".join("echo setup%d" % n for n in range(9))
            + "\n\npixi run deploy --environment production \\\n"
            + "  --region us-east-1\n```\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        hit = search(self.db, "deploy production")[0]
        self.assertLessEqual(len(hit["excerpt"].splitlines()), 8)
        self.assertTrue(hit["excerpt"].startswith("pixi run deploy"))
        self.assertIn("--region us-east-1", hit["excerpt"])

    def test_git_ignored_docs_are_excluded(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("private.md\n", encoding="utf-8")
        (self.root / "private.md").write_text("# Secret\nHidden instructions.\n", encoding="utf-8")
        (self.root / "public.md").write_text("# Public\nVisible instructions.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        self.assertFalse(search(self.db, "hidden"))
        self.assertTrue(search(self.db, "visible"))

    def test_search_command_returns_matching_passage(self):
        (self.root / "install.md").write_text("# Install\nRun the setup command.\n", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["search", "setup", "--root", str(self.root), "--index", str(Path(self.temp.name) / "cli.sqlite3")])
        self.assertEqual(status, 0)
        self.assertIn("install.md:2 [Install]", output.getvalue())

    def test_s_alias_returns_matching_passage(self):
        (self.root / "install.md").write_text("# Install\nRun the setup command.\n", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["s", "setup", "--root", str(self.root),
                           "--index", str(Path(self.temp.name) / "cli.sqlite3")])
        self.assertEqual(status, 0)
        self.assertIn("install.md:2 [Install]", output.getvalue())

    def test_search_styles_tty_but_respects_no_color(self):
        (self.root / "install.md").write_text("# Install\nRun the setup command.\n", encoding="utf-8")
        args = ["search", "setup", "--root", str(self.root), "--index", str(Path(self.temp.name) / "cli.sqlite3")]
        styled = TtyBuffer()
        with patch.dict(os.environ, {"NO_COLOR": ""}), redirect_stdout(styled):
            main(args)
        self.assertIn("\x1b[1;36minstall.md:2 [Install]\x1b[0m", styled.getvalue())
        self.assertIn("\x1b]8;;{}#L2\x1b\\".format((self.root / "install.md").resolve().as_uri()), styled.getvalue())
        plain = TtyBuffer()
        with patch.dict(os.environ, {"NO_COLOR": "1"}), redirect_stdout(plain):
            main(args)
        self.assertNotIn("\x1b[", plain.getvalue())
        self.assertNotIn("\x1b]8;", plain.getvalue())

    def test_search_limits_ranked_results(self):
        for name in ("one.md", "two.md", "three.md"):
            (self.root / name).write_text("# Install\nRun setup.\n", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["search", "setup", "--limit", "2", "--root", str(self.root),
                                   "--index", str(Path(self.temp.name) / "cli.sqlite3")]), 0)
        self.assertEqual(output.getvalue().count("[Install]"), 2)

    def test_interactive_search_leaves_document_view_as_final_output(self):
        (self.root / "one.md").write_text("# First\nsetup first step\nsecond step\n", encoding="utf-8")
        (self.root / "two.md").write_text("# Second\nsetup another step\nlast step\n", encoding="utf-8")
        output = TtyBuffer()
        with patch.object(sys, "stdin", TtyBuffer()), \
                patch("nemd.cli._choose_result") as choose, \
                redirect_stdout(output):
            self.assertEqual(main(["search", "setup", "--root", str(self.root),
                                   "--index", str(Path(self.temp.name) / "cli.sqlite3")]), 0)
        self.assertEqual(len(choose.call_args.args[0]), 2)
        self.assertEqual(choose.call_args.args[1], self.root.resolve())
        self.assertEqual(output.getvalue(), "")

    def test_interactive_search_allows_cancelling_selection(self):
        (self.root / "one.md").write_text("# First\nsetup first step\nsecond step\n", encoding="utf-8")
        output = TtyBuffer()
        with patch.object(sys, "stdin", TtyBuffer()), patch("nemd.cli._choose_result", return_value=None), \
                redirect_stdout(output):
            self.assertEqual(main(["search", "setup", "--root", str(self.root),
                                   "--index", str(Path(self.temp.name) / "cli.sqlite3")]), 0)
        self.assertEqual(output.getvalue(), "")

    def test_picker_scrolls_with_arrows_and_tab(self):
        results = [{"path": "doc%d.md" % n, "line": n, "heading": "Topic %d" % n,
                    "excerpt": "Preview %d" % n} for n in range(5)]
        keys = iter(["down", "down", "choose"])
        output = io.StringIO()
        self.assertIs(_pick_result(results, lambda: next(keys), output, 80, 6), results[2])
        self.assertIn("> doc2.md", output.getvalue())
        self.assertIn("\x1b[3A", output.getvalue())
        colored = io.StringIO()
        self.assertIs(_pick_result(results, lambda: "choose", colored, 80, 6, color=True), results[0])
        self.assertIn("\x1b[1;36mdoc0.md:0 [Topic 0]\x1b[0m - Preview 0", colored.getvalue())
        keys = iter(["down", "down", "down", "choose"])
        self.assertIs(_pick_result(results, lambda: next(keys), io.StringIO(), 80, 6), results[3])

    def test_picker_wraps_and_cancels(self):
        results = [{"path": "doc%d.md" % n, "line": n, "heading": "", "excerpt": "Preview"}
                   for n in range(3)]
        keys = iter(["up", "choose"])
        self.assertIs(_pick_result(results, lambda: next(keys), io.StringIO(), 80, 6), results[-1])
        output = io.StringIO()
        self.assertIsNone(_pick_result(results, lambda: "cancel", output, 80, 6))
        self.assertIn("doc0.md", output.getvalue())
        self.assertTrue(output.getvalue().endswith("\r\n"))
        self.assertNotIn("\x1b[J", output.getvalue())

    def test_clicking_result_opens_that_match(self):
        results = [{"path": "doc%d.md" % n, "line": n, "heading": "", "excerpt": "Preview"}
                   for n in range(3)]
        chosen = _pick_result(results, lambda: ("click", 8, 11), io.StringIO(), 80, 6,
                              cursor_row=lambda: 12)
        self.assertIs(chosen, results[1])

    def test_reads_mouse_click_and_arrow_keys(self):
        for sequence, expected in ((b"\x1b[<0;8;11M", ("click", 8, 11)),
                                   (b"\x1b[B", "down"), (b"\x1b[6~", "page_down")):
            reader, writer = os.pipe()
            try:
                os.write(writer, sequence)
                self.assertEqual(_read_key(reader), expected)
            finally:
                os.close(reader)
                os.close(writer)

    def test_expanded_result_starts_near_match_and_scrolls(self):
        (self.root / "guide.md").write_text(
            "\n".join("line %d" % n for n in range(1, 31)) + "\n", encoding="utf-8")
        hit = {"path": "guide.md", "line": 15, "heading": "Guide", "excerpt": "line 15"}
        output = io.StringIO()
        keys = iter(["down", "page_down", "up", "back"])
        self.assertEqual(_view_result(hit, self.root, lambda: next(keys), output, 80, 10), "back")
        self.assertIn("guide.md:15 [Guide]", output.getvalue())
        self.assertIn("> line 15", output.getvalue())
        self.assertNotIn("    15  line 15", output.getvalue())
        self.assertIn("line 21", output.getvalue())
        self.assertLess(output.getvalue().index("line 30"), output.getvalue().index("guide.md:15 [Guide]"))
        self.assertTrue(output.getvalue().endswith("\r\x1b[2K\r\n"))
        self.assertNotIn("\x1b[J", output.getvalue())

    def test_expanded_result_can_close_with_escape_or_enter(self):
        (self.root / "guide.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
        hit = {"path": "guide.md", "line": 2, "heading": "", "excerpt": "two"}
        for key in ("cancel", "choose"):
            with self.subTest(key=key):
                output = io.StringIO()
                self.assertEqual(_view_result(hit, self.root, lambda: key, output, 80, 10), key)
                self.assertIn("Enter/Esc: close", output.getvalue())
                self.assertTrue(output.getvalue().endswith("\r\x1b[2K\r\n"))

    def test_expanded_result_highlights_markdown_without_line_numbers(self):
        (self.root / "guide.md").write_text(
            "# Title\nParagraph with `command`.\n## Details\n> Quoted text\n- List item\n"
            "```sh\necho hello\n```\n", encoding="utf-8")
        hit = {"path": "guide.md", "line": 7, "heading": "Details", "excerpt": "echo hello"}
        styled = io.StringIO()
        self.assertEqual(_view_result(hit, self.root, lambda: "cancel", styled, 80, 20, color=True),
                         "cancel")
        shown = styled.getvalue()
        for fragment in ("\x1b[1;36m## Details\x1b[0m", "\x1b[3;32m> Quoted text\x1b[0m",
                         "\x1b[33m- List item\x1b[0m", "\x1b[2m```sh\x1b[0m",
                         "\x1b[32mecho hello\x1b[0m", "\x1b[32m`command`\x1b[0m"):
            self.assertIn(fragment, shown)
        self.assertIn("> \x1b[32mecho hello\x1b[0m", shown)
        plain = io.StringIO()
        _view_result(hit, self.root, lambda: "cancel", plain, 80, 20, color=False)
        self.assertNotIn("\x1b[32m", plain.getvalue())

    def test_code_style_survives_scrolling_past_fence(self):
        lines = ["```python", "one", "two", "three", "four", "five", "```", "## Next"]
        self.assertEqual(_markdown_kinds(lines),
                         ["fence", "code", "code", "code", "code", "code", "fence", "heading"])

    def test_json_remains_machine_readable_on_tty(self):
        (self.root / "install.md").write_text("# Install\nRun the setup command.\n", encoding="utf-8")
        output = TtyBuffer()
        with redirect_stdout(output):
            main(["search", "setup", "--json", "--root", str(self.root), "--index", str(Path(self.temp.name) / "cli.sqlite3")])
        self.assertEqual(json.loads(output.getvalue())[0]["path"], "install.md")


if __name__ == "__main__":
    unittest.main()
