# Urag

Urag is a local CLI for searching documentation in a project. It indexes Markdown, MDX, reStructuredText, AsciiDoc, and plain text, then returns matching passages with file paths and line numbers. Fetch refreshes the index automatically, so edited docs appear in the next query.

## Install and use

Requires Python 3.9+ with SQLite FTS5 (included in common Python builds).

Install from this source checkout. `uv pip install urag` fetches an unrelated package already published on PyPI; this project's distribution is named `urag-fetch`, while its command remains `urag`.

```sh
cd /path/to/doc-engine
uv tool install --editable .
cd /path/to/your/project
urag fetch "How to compile with fast settings?"
urag fetch "deployment options" --limit 5 --json
urag index
```

If `urag` is not on your `PATH` after installation, run `uv tool update-shell` and open a new terminal. This installs one tool for your user account; searched projects do not need their own virtual environments or Urag dependency. The index remains separate for each project.

For development in this source repository, an editable project environment also works:

```sh
uv venv
uv pip install -e .
./.venv/bin/urag fetch "How to install"
```

Alternatively, run `source .venv/bin/activate` first; then `urag` is available on your shell's `PATH` until you deactivate the environment.

You can search another project with `--root /path/to/project`. `python3 -m urag fetch ...` also works when Urag is installed in that Python environment.

The index lives in `~/.cache/urag` by default, outside the searched project. Set `URAG_CACHE_DIR` or pass `--index PATH` to choose another location. In Git repos, Urag respects ignored untracked files. In directories without Git, it skips common generated and dependency directories. Files larger than 2 MB are skipped.

Fetch uses SQLite FTS5 with heading-weighted ranking and a small set of common development term aliases. If a query has no results, it tries close spellings of indexed terms. Everything runs offline; no document text leaves your machine. This first version does lexical search, so questions phrased with concepts absent from the docs may need different wording.

## Optional semantic graph

For concept matching, Urag can build a local semantic graph from the same documentation sections and use a contextual token encoder at query time. It stores document, chunk, token-meaning, and chunk-to-meaning links in the SQLite index. `fetch --semantic` refreshes changed docs and the graph, then constructs a query-specific co-occurrence graph to rank sections. The default `fetch` behavior remains lexical.

### Recommended model

The recommended encoder is [Microsoft's DeBERTa-v3-large](https://huggingface.co/microsoft/deberta-v3-large). LiteSemRAG used DeBERTa-v3-large for contextual token embeddings; it does not provide a separate LiteSemRAG model checkpoint. [LiteSemRAG paper](https://arxiv.org/html/2604.16350v1)

### Setup

From the `urag-fetch` source root, install Urag with its optional semantic dependencies, then download the model files into the user cache. The download command selects the PyTorch weights and tokenizer files needed by Urag:

```sh
uv tool install --editable '.[semantic]'
uv tool run --from huggingface_hub hf download microsoft/deberta-v3-large \
  config.json pytorch_model.bin spm.model tokenizer_config.json \
  --local-dir ~/.cache/urag/models/deberta-v3-large
```

If you use the development `.venv` instead, run `uv pip install -e '.[semantic]'` and invoke `./.venv/bin/urag`. The model download needs an internet connection; indexing and fetching load only local files and do not call an API.

If Urag was installed before `protobuf` was added to the semantic dependencies, run `uv tool install --force --editable '.[semantic]'` from this source root to update its tool environment. A missing `protobuf` package can cause Transformers to fail while converting DeBERTa's SentencePiece tokenizer.

When the DeBERTa checkpoint loads as an encoder, Urag condenses its unused prediction-head weight report into one line. Reports about missing or other unexpected weights remain visible.

### Index and fetch

After setup, change into the project whose documentation you want to search. Urag uses the current directory as the project root by default:

```sh
cd /path/to/your/project
urag config set semantic-model ~/.cache/urag/models/deberta-v3-large
urag index --semantic
urag fetch "How do I run an evaluation with SQL sampling?" \
  --semantic
```

The config command saves the model directory in `~/.config/urag/config.json` (or under `XDG_CONFIG_HOME`). Use `urag config get semantic-model` to inspect it or `urag config unset semantic-model` to remove it. The key `URAG_SEMANTIC_MODEL` is also accepted by the config command. For a one-off model, `--model PATH` takes precedence; the `URAG_SEMANTIC_MODEL` environment variable overrides the saved setting. Replace the example query with a question about that project's docs. The first graph build encodes every indexed section and may be slow with a large model. A changed document currently rebuilds the semantic graph for the whole repository. The graph uses a simple similarity threshold to separate token meanings, rather than the paper's HDBSCAN and adaptive anomaly handling, so this is a LiteSemRAG-inspired implementation, not an exact reproduction. Semantic relevance depends on the chosen encoder and should be measured on your own queries.

Terminal results highlight the source location and separate matches with a blank line. Redirected output and `NO_COLOR=1` use plain text; `--json` always emits unstyled JSON. Formatting has no external dependencies.

Result excerpts show up to eight source lines. A matching code block is shown in full when it fits; longer blocks show the relevant command stanza.

## Development

```sh
python3 -m unittest discover -s tests
```
