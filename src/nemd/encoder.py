"""Contextual token vectors from a locally stored Hugging Face model."""

import hashlib
import logging
import sys
from pathlib import Path


class _LoadReportFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.records = []

    def filter(self, record):
        if " LOAD REPORT" in record.getMessage():
            self.records.append(record)
            return False
        return True


def _load_model(auto_model, path):
    logger = logging.getLogger("transformers.modeling_utils")
    report_filter = _LoadReportFilter()
    logger.addFilter(report_filter)
    try:
        try:
            model, info = auto_model.from_pretrained(
                str(path), local_files_only=True, output_loading_info=True
            )
        finally:
            logger.removeFilter(report_filter)
    except Exception:
        for record in report_filter.records:
            logger.handle(record)
        raise
    unexpected = info.get("unexpected_keys", ())
    other_issues = any(info.get(key) for key in ("missing_keys", "mismatched_keys", "error_msgs"))
    expected_heads = all(key.startswith(("mask_predictions.", "lm_predictions.")) for key in unexpected)
    if unexpected and expected_heads and not other_issues:
        print("nemd: model loaded; ignored %d unused prediction-head weights." % len(unexpected),
              file=sys.stderr)
    else:
        for record in report_filter.records:
            logger.handle(record)
    return model


class LocalTokenEncoder:
    def __init__(self, model_path):
        self.path = Path(model_path).expanduser().resolve()
        if not self.path.is_dir():
            raise ValueError("semantic model directory does not exist: %s" % self.path)
        try:
            import torch
            from transformers import AutoConfig, AutoModel, AutoTokenizer
        except ImportError as error:
            raise ValueError("semantic search requires the optional torch and transformers packages") from error
        self.torch = torch
        config = AutoConfig.from_pretrained(str(self.path), local_files_only=True)
        tokenizer_options = {}
        if config.model_type == "deberta-v2":
            # Some Transformers versions mistake local DeBERTa checkpoints for
            # Mistral and emit an irrelevant regex warning. Do not apply the
            # Mistral tokenizer rewrite to this model.
            tokenizer_options["fix_mistral_regex"] = False
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(self.path), use_fast=True, local_files_only=True, **tokenizer_options
        )
        if not self.tokenizer.is_fast:
            raise ValueError("semantic search requires a fast tokenizer with character offsets")
        self.model = _load_model(AutoModel, self.path)
        self.model.eval()
        signature = hashlib.sha256()
        for path in sorted(self.path.iterdir()):
            if path.is_file():
                stat = path.stat()
                signature.update(path.name.encode())
                signature.update(str((stat.st_size, stat.st_mtime_ns)).encode())
        self.model_id = "%s:%s" % (self.path, signature.hexdigest()[:16])

    def encode(self, text, spans):
        if not spans:
            return []
        vectors = [None] * len(spans)
        first = 0
        while first < len(spans):
            last = first + 1
            while last < len(spans) and spans[last][1] - spans[first][0] <= 400:
                last += 1
            start_offset = spans[first][0]
            end_offset = spans[last - 1][1]
            encoding = self.tokenizer(
                text[start_offset:end_offset], return_offsets_mapping=True,
                return_tensors="pt", truncation=True, max_length=512,
            )
            offsets = encoding.pop("offset_mapping")[0].tolist()
            with self.torch.no_grad():
                hidden = self.model(**encoding).last_hidden_state[0]
            for index in range(first, last):
                start, end = spans[index]
                start -= start_offset
                end -= start_offset
                positions = [position for position, (left, right) in enumerate(offsets)
                             if left < end and right > start]
                if not positions:
                    raise ValueError("semantic tokenizer could not encode a document token")
                vectors[index] = hidden[positions].mean(dim=0).tolist()
            first = last
        return vectors
