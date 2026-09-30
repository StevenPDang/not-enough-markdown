import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nemd.cli import main


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model = Path(self.temp.name) / "model"
        self.model.mkdir()
        self.config_dir = Path(self.temp.name) / "config"

    def run_config(self, *args):
        output = io.StringIO()
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config_dir)}), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            status = main(["config", *args])
        return status, output.getvalue()

    def test_set_get_and_unset_semantic_model(self):
        self.assertEqual(self.run_config("set", "semantic-model", str(self.model))[0], 0)
        self.assertTrue((self.config_dir / "nemd" / "config.json").is_file())
        self.assertEqual(self.run_config("get", "NEMD_SEMANTIC_MODEL"), (0, str(self.model.resolve()) + "\n"))
        self.assertEqual(self.run_config("unset", "semantic-model")[0], 0)
        self.assertEqual(self.run_config("get", "semantic-model")[0], 1)

    def test_semantic_index_uses_saved_model(self):
        self.run_config("set", "semantic-model", str(self.model))
        repo = Path(self.temp.name) / "repo"
        repo.mkdir()
        args = ["index", "--semantic", "--root", str(repo),
                "--index", str(Path(self.temp.name) / "index.sqlite3")]
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config_dir), "NEMD_SEMANTIC_MODEL": ""}), \
                patch("nemd.encoder.LocalTokenEncoder") as encoder, \
                patch("nemd.cli.build_graph"), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        encoder.assert_called_once_with(str(self.model.resolve()))

    def test_flag_and_environment_override_saved_model(self):
        self.run_config("set", "semantic-model", str(self.model))
        repo = Path(self.temp.name) / "repo"
        repo.mkdir()
        args = ["index", "--semantic", "--root", str(repo),
                "--index", str(Path(self.temp.name) / "index.sqlite3")]
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config_dir), "NEMD_SEMANTIC_MODEL": "env-model"}), \
                patch("nemd.encoder.LocalTokenEncoder") as encoder, \
                patch("nemd.cli.build_graph"), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
            encoder.assert_called_with("env-model")
            self.assertEqual(main(args + ["--model", "flag-model"]), 0)
            encoder.assert_called_with(Path("flag-model"))


if __name__ == "__main__":
    unittest.main()
