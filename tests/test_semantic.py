import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from urag.search import open_index, refresh_index, search
from urag.semantic import build_graph, graph_ready, semantic_search
from urag.cli import main


class FakeEncoder:
    model_id = "test-context-encoder"

    def encode(self, text, spans):
        vectors = []
        for start, end in spans:
            word = text[start:end].lower()
            if word in ("evaluation", "inference"):
                vectors.append([1.0, 0.0, 0.0])
            elif word == "sql":
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class SemanticGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.db = open_index(Path(self.temp.name) / "index.sqlite3")
        self.addCleanup(self.db.close)
        self.encoder = FakeEncoder()

    def test_builds_and_rebuilds_graph_when_docs_change(self):
        doc = self.root / "guide.mdx"
        doc.write_text("# Sampling\nRun SQL inference.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        self.assertTrue(build_graph(self.db, self.encoder))
        self.assertTrue(graph_ready(self.db, self.encoder.model_id))
        self.assertFalse(build_graph(self.db, self.encoder))
        doc.write_text("# Sampling\nRun SQL evaluation now.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        self.assertFalse(graph_ready(self.db, self.encoder.model_id))
        self.assertTrue(build_graph(self.db, self.encoder))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM semantic_chunks").fetchone()[0], 1)

    def test_query_graph_connects_evaluation_to_inference(self):
        (self.root / "sampling.md").write_text(
            "# SQL sampling\nRun inference with SQL sampling.\n", encoding="utf-8")
        (self.root / "other.md").write_text(
            "# Evaluation\nRun evaluation with random seeds.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        build_graph(self.db, self.encoder)
        hits = semantic_search(self.db, "evaluation sql sampling", self.encoder)
        self.assertEqual(hits[0]["path"], "sampling.md")

    def test_semantic_only_match_without_shared_words(self):
        (self.root / "sampling.md").write_text("# Inference\nInference steps.\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        build_graph(self.db, self.encoder)
        self.assertFalse(search(self.db, "evaluation"))
        self.assertEqual(semantic_search(self.db, "evaluation", self.encoder)[0]["path"], "sampling.md")

    def test_run_query_prefers_matching_eval_command(self):
        (self.root / "eval.md").write_text(
            "# Evaluate on RelBench\nRun the evaluation:\n\n```sh\n"
            "pixi run eval --checkpoint rt-j \\\n"
            "  --pre-dir relbench-preprocessed \\\n"
            "  --out-dir eval_out\n```\n", encoding="utf-8")
        (self.root / "pretrain.md").write_text(
            "# Pretrain on RelBench\nRun pretraining on RelBench.\n\n```sh\n"
            "pixi run pretrain --val-pre-dir relbench-preprocessed\n```\n", encoding="utf-8")
        (self.root / "rescore.md").write_text(
            "# Evaluate with the RelBench evaluator\nRescore an existing submission.\n\n```sh\n"
            "pixi run python -m relbench.leaderboard eval_out\n```\n", encoding="utf-8")
        refresh_index(self.db, self.root)
        build_graph(self.db, self.encoder)
        hits = semantic_search(self.db, "How do I run an evalutation against RelBench?", self.encoder)
        self.assertEqual(hits[0]["path"], "eval.md")
        self.assertEqual(hits[0]["line"], 5)
        self.assertIn("\n  --pre-dir relbench-preprocessed", hits[0]["excerpt"])
        self.assertIn("\n  --out-dir eval_out", hits[0]["excerpt"])

    def test_fetch_builds_graph_and_queries_it(self):
        (self.root / "sampling.md").write_text(
            "# SQL sampling\nRun inference with SQL sampling.\n", encoding="utf-8")
        output = io.StringIO()
        args = ["fetch", "evaluation sql sampling", "--semantic", "--model", "local-model",
                "--root", str(self.root), "--index", str(Path(self.temp.name) / "cli.sqlite3")]
        with patch("urag.encoder.LocalTokenEncoder", return_value=self.encoder), redirect_stdout(output):
            self.assertEqual(main(args), 0)
        self.assertIn("sampling.md", output.getvalue())


if __name__ == "__main__":
    unittest.main()
