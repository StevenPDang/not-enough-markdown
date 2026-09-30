# Not Enough Markdown

nemd is a CLI you can install with `uv` to fuzzy search documentation in a codebase. It builds a local SQLite index of document sections, including Markdown headings, code blocks, and identifiers, then returns the top `k` matching passages with source paths and line numbers. Search refreshes the index automatically when documents change. Named after the popular Minecraft mod Not Enough Items (NEI)!

## Features

- Search Markdown, MDX, reStructuredText, AsciiDoc, and plain-text documentation with ranked excerpts and `path:line` locations.
- Open a result in an interactive terminal viewer, or use `--json` for machine-readable output.
- Refresh changed documents automatically; match common development terms and close spellings.
- **Experimental semantic search:** use an optional local model for concept matching.

## Demo

![nemd search demo](docs/demo.gif)

Demo repository: [RelBench by Stanford STAR](https://github.com/stanford-star/relbench).

## Getting Started

Requires Python 3.9+ with SQLite FTS5 (included in common Python builds).

### Install

Install from this source checkout:

```sh
cd /path/to/your/nemd/checkout
uv tool install --editable .
```

If `nemd` is not on your `PATH`, run `uv tool update-shell` and open a new terminal. The tool is installed for your user account; projects you search do not need their own nemd installation.

### Search

From the project whose documentation you want to search:

```sh
cd /path/to/your/project
nemd search "How do I configure deployment?"
nemd s "build settings" --limit 5
nemd search "deployment options" --json
```

`nemd s` is shorthand for `nemd search`. By default, search uses the current directory and returns up to 10 results. Use `--root PATH` to search another project. Each result includes an excerpt and a source location. In an interactive terminal, choose a result to read the document near the match; use Backspace to return to results or Enter or Esc to close. Redirected output and `NO_COLOR=1` use plain text.

Search uses SQLite FTS5. It runs offline and does not send document text to an API. In Git repositories, it respects ignored untracked files. Outside Git, it skips common generated and dependency directories. Files larger than 2 MB are skipped.

### Indexing

An index is a per-project SQLite database of searchable document sections, headings, file paths, and line numbers. `nemd search` creates or refreshes it automatically before each query, including changes and deletions. It does not modify the source documents.

Run `nemd index` if you want to prepare the index ahead of time or see how many documents changed:

```sh
nemd index
```

Indexes live outside the project in `~/.cache/nemd` by default. Set `NEMD_CACHE_DIR` or pass `--index PATH` to use another location.

### Experimental semantic search

Semantic search builds a local graph from the indexed sections and uses a contextual token encoder to match concepts. It requires an optional model download; ordinary `nemd search` remains lexical. Relevance depends on the model and your project's documents, so evaluate results against your own queries.

The recommended encoder is [Microsoft's DeBERTa-v3-large](https://huggingface.co/microsoft/deberta-v3-large). Install the optional dependencies from this checkout and download the model:

```sh
cd /path/to/your/nemd/checkout
uv tool install --force --editable '.[semantic]'
uv tool run --from huggingface_hub hf download microsoft/deberta-v3-large \
  config.json pytorch_model.bin spm.model tokenizer_config.json \
  --local-dir ~/.cache/nemd/models/deberta-v3-large
```

Then search a project with the model:

```sh
cd /path/to/your/project
nemd config set semantic-model ~/.cache/nemd/models/deberta-v3-large
nemd search "How do I run an evaluation with SQL sampling?" --semantic
```

The model setting is saved in `~/.config/nemd/config.json` (or under `XDG_CONFIG_HOME`). Use `nemd config get semantic-model` or `nemd config unset semantic-model` to inspect or remove it. For one query, `--model PATH` takes precedence over `NEMD_SEMANTIC_MODEL`, which takes precedence over the saved setting. The first graph build can be slow, and changing a document currently rebuilds the whole semantic graph. This is a LiteSemRAG-inspired implementation, rather than an exact reproduction of the [paper](https://arxiv.org/html/2604.16350v1).

### Development

```sh
uv venv
uv pip install -e .
./.venv/bin/python -m unittest discover -s tests
```
