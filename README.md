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

Terminal results highlight the source location and separate matches with a blank line. Redirected output and `NO_COLOR=1` use plain text; `--json` always emits unstyled JSON. Formatting has no external dependencies.

## Development

```sh
python3 -m unittest discover -s tests
```
