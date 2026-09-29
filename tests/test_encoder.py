import io
import logging
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from urag.encoder import _load_model


class ModelLoadReportTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("transformers.modeling_utils")
        self.output = io.StringIO()
        self.handler = logging.StreamHandler(self.output)
        self.old_level = self.logger.level
        self.old_propagate = self.logger.propagate
        self.logger.setLevel(logging.WARNING)
        self.logger.propagate = False
        self.logger.addHandler(self.handler)
        self.addCleanup(self.restore_logger)

    def restore_logger(self):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.old_level)
        self.logger.propagate = self.old_propagate

    def fake_model(self, unexpected, missing=(), error=None):
        logger = self.logger

        class AutoModel:
            @staticmethod
            def from_pretrained(*args, **kwargs):
                logger.warning("DebertaV2Model LOAD REPORT from: model\nKey | Status")
                if error:
                    raise error
                return object(), {"unexpected_keys": set(unexpected), "missing_keys": set(missing),
                                  "mismatched_keys": set(), "error_msgs": []}

        return AutoModel

    def test_summarizes_unused_prediction_head_weights(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            _load_model(self.fake_model(["mask_predictions.dense.weight",
                                         "lm_predictions.lm_head.bias"]), Path("model"))
        self.assertIn("ignored 2 unused prediction-head weights", stderr.getvalue())
        self.assertNotIn("LOAD REPORT", self.output.getvalue())
        self.assertEqual(self.logger.filters, [])

    def test_preserves_report_for_other_weight_issues(self):
        with redirect_stderr(io.StringIO()):
            _load_model(self.fake_model(["encoder.layer.0.weight"]), Path("model"))
        self.assertIn("LOAD REPORT", self.output.getvalue())
        self.output.seek(0)
        self.output.truncate()
        with redirect_stderr(io.StringIO()):
            _load_model(self.fake_model([], missing=["encoder.layer.0.weight"]), Path("model"))
        self.assertIn("LOAD REPORT", self.output.getvalue())

    def test_preserves_report_when_load_fails(self):
        with self.assertRaisesRegex(RuntimeError, "load failed"):
            _load_model(self.fake_model([], error=RuntimeError("load failed")), Path("model"))
        self.assertIn("LOAD REPORT", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
