# Not Enough Markdown

nemd is a local CLI for searching documentation in a project. Its name is a nod to the Minecraft mod Not Enough Items (NEI). It indexes Markdown, MDX, reStructuredText, AsciiDoc, and plain text, then returns matching passages with file paths and line numbers. Search refreshes the index automatically, so edited docs appear in the next query.

## Features

- Local search across Markdown, MDX, reStructuredText, AsciiDoc, and plain text.
- Ranked results with excerpts, source paths, line numbers, and an interactive terminal viewer.
- Automatic index refresh, typo matching, and offline operation.
- **Experimental semantic search:** optional local model and semantic graph for concept matching.

## Demo

From a project directory, search its documentation:

```sh
nemd search "deployment options"
nemd s "build settings" --limit 5
```

Results include a `path:line` location and an excerpt. In a terminal, choose a result to read the full document near the match.

## Getting Started

Requires Python 3.9+ with SQLite FTS5 (included in common Python builds).

Install from this source checkout:

```sh
cd /path/to/doc-engine
uv tool install --editable .
cd /path/to/your/project
nemd search "How to compile with fast settings?"
nemd search "deployment options" --limit 5 --json
nemd index
```

Use `nemd s "query"` as shorthand for `nemd search "query"`.

If `nemd` is not on your `PATH` after installation, run `uv tool update-shell` and open a new terminal. This installs one tool for your user account; searched projects do not need their own virtual environments or Nemd dependency. The index remains separate for each project.

If you previously installed the CLI under its old name, reinstall from this checkout to create the `nemd` command. The new cache and config paths are separate; set `semantic-model` again if you had saved one under the old name. Existing model files can be reused by passing their path to `nemd config set semantic-model`.

For development in this source repository, an editable project environment also works:

```sh
uv venv
uv pip install -e .
./.venv/bin/nemd search "How to install"
```

Alternatively, run `source .venv/bin/activate` first; then `nemd` is available on your shell's `PATH` until you deactivate the environment.

You can search another project with `--root /path/to/project`. `python3 -m nemd search ...` also works when Nemd is installed in that Python environment.

The index lives in `~/.cache/nemd` by default, outside the searched project. Set `NEMD_CACHE_DIR` or pass `--index PATH` to choose another location. In Git repos, Nemd respects ignored untracked files. In directories without Git, it skips common generated and dependency directories. Files larger than 2 MB are skipped.

Search uses SQLite FTS5 with heading-weighted ranking and a small set of common development term aliases. If a query has no results, it tries close spellings of indexed terms. Everything runs offline; no document text leaves your machine. This first version does lexical search, so questions phrased with concepts absent from the docs may need different wording.

Search returns the top 10 ranked matches by default; use `--limit K` to choose how many candidates to see. In an interactive terminal, an inline prompt shows their source locations and short previews. Use the arrow keys or Tab to move through the choices, then Enter or a mouse click to expand a match. Nemd writes the entire rendered document to terminal scrollback, then opens a focused view near the matched line. Scroll with the arrow or Page keys, use Backspace to return to the results, or Enter or Esc to close. The controls line clears when you leave the view. The reading position remains visible when you exit, and the complete document remains in scrollback for copying or reference. Markdown headings, code blocks, quotes, lists, and inline code are highlighted without a line number gutter. Mouse selection requires a terminal that supports mouse reporting. Redirected output and `NO_COLOR=1` use plain text; `--json` always emits unstyled JSON. Formatting has no external dependencies.

Result excerpts show up to eight source lines. A matching code block is shown in full when it fits; longer blocks show the relevant command stanza.

### Experimental semantic search

For concept matching, Nemd can build a local semantic graph from the same documentation sections and use a contextual token encoder at query time. It stores document, chunk, token-meaning, and chunk-to-meaning links in the SQLite index. `search --semantic` refreshes changed docs and the graph, then constructs a query-specific co-occurrence graph to rank sections. The default `search` behavior remains lexical.

#### Recommended model

The recommended encoder is [Microsoft's DeBERTa-v3-large](https://huggingface.co/microsoft/deberta-v3-large). LiteSemRAG used DeBERTa-v3-large for contextual token embeddings; it does not provide a separate LiteSemRAG model checkpoint. [LiteSemRAG paper](https://arxiv.org/html/2604.16350v1)

#### Setup

From the `nemd` source root, install Nemd with its optional semantic dependencies, then download the model files into the user cache. The download command selects the PyTorch weights and tokenizer files needed by Nemd:

```sh
uv tool install --editable '.[semantic]'
uv tool run --from huggingface_hub hf download microsoft/deberta-v3-large \
  config.json pytorch_model.bin spm.model tokenizer_config.json \
  --local-dir ~/.cache/nemd/models/deberta-v3-large
```

If you use the development `.venv` instead, run `uv pip install -e '.[semantic]'` and invoke `./.venv/bin/nemd`. The model download needs an internet connection; indexing and searching load only local files and do not call an API.

If an earlier editable install lacks `protobuf`, run `uv tool install --force --editable '.[semantic]'` from this source root to update its tool environment. A missing `protobuf` package can cause Transformers to fail while converting DeBERTa's SentencePiece tokenizer.

When the DeBERTa checkpoint loads as an encoder, Nemd condenses its unused prediction-head weight report into one line. Reports about missing or other unexpected weights remain visible.

#### Index and search

After setup, change into the project whose documentation you want to search. Nemd uses the current directory as the project root by default:

```sh
cd /path/to/your/project
nemd config set semantic-model ~/.cache/nemd/models/deberta-v3-large
nemd index --semantic
nemd search "How do I run an evaluation with SQL sampling?" \
  --semantic
```

The config command saves the model directory in `~/.config/nemd/config.json` (or under `XDG_CONFIG_HOME`). Use `nemd config get semantic-model` to inspect it or `nemd config unset semantic-model` to remove it. The key `NEMD_SEMANTIC_MODEL` is also accepted by the config command. For a one-off model, `--model PATH` takes precedence; the `NEMD_SEMANTIC_MODEL` environment variable overrides the saved setting. Replace the example query with a question about that project's docs. The first graph build encodes every indexed section and may be slow with a large model. A changed document currently rebuilds the semantic graph for the whole repository. The graph uses a simple similarity threshold to separate token meanings, rather than the paper's HDBSCAN and adaptive anomaly handling, so this is a LiteSemRAG-inspired implementation, not an exact reproduction. Semantic relevance depends on the chosen encoder and should be measured on your own queries.

### Development

```sh
python3 -m unittest discover -s tests
```
