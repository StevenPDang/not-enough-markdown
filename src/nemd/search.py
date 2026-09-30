"""Incremental, local full-text search over project documentation."""

import difflib
import hashlib
import os
import re
import sqlite3
import subprocess
from pathlib import Path


EXTENSIONS = {".md", ".mdx", ".markdown", ".rst", ".adoc", ".txt"}
NAME_ONLY = {"readme", "changelog", "contributing", "license"}
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__"}
STOPWORDS = {"a", "an", "and", "are", "can", "do", "for", "from", "how", "i", "in", "is", "it", "of", "on", "the", "to", "use", "using", "what", "when", "where", "with"}
ALIASES = {
    "compile": ("build",), "build": ("compile",),
    "config": ("configure", "settings", "options"),
    "configure": ("config", "settings", "options"),
    "settings": ("config", "configure", "options", "flags"),
    "flags": ("options", "settings"),
}
WORD = re.compile(r"\w+", re.UNICODE)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
MAX_FILE_BYTES = 2_000_000
SCHEMA_VERSION = 1


def default_index_path(root):
    cache = Path(os.environ.get("NEMD_CACHE_DIR", Path.home() / ".cache" / "nemd"))
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    return cache / (key + ".sqlite3")


def open_index(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        db.execute("DROP TABLE IF EXISTS vocabulary")
        db.execute("DROP TABLE IF EXISTS chunks")
        db.execute("DROP TABLE IF EXISTS files")
        db.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    db.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER)")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(path, line UNINDEXED, heading, body, tokenize='porter unicode61')")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vocabulary USING fts5vocab(chunks, 'row')")
    return db


def documentation_files(root):
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        )
        candidates = (root / os.fsdecode(name) for name in proc.stdout.split(b"\0") if name)
    except (FileNotFoundError, subprocess.CalledProcessError):
        def walk():
            for base, dirs, files in os.walk(root):
                dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
                for name in files:
                    yield Path(base) / name
        candidates = walk()
    for path in candidates:
        if path.suffix.lower() not in EXTENSIONS and path.name.lower() not in NAME_ONLY:
            continue
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
            yield path


def sections(content):
    heading = ""
    body = []
    fenced = False

    def flush():
        nonlocal body
        while body and not body[0][1].strip():
            body.pop(0)
        if body:
            yield body[0][0], heading, "\n".join(text for _, text in body)
        body = []

    for line_no, text in enumerate(content.splitlines(), 1):
        if text.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = HEADING.match(text) if not fenced else None
        if match:
            yield from flush()
            heading = match.group(1).strip()
            continue
        if body and sum(len(item[1]) for item in body) > 1800 and not text.strip():
            yield from flush()
        body.append((line_no, text))
    yield from flush()


def refresh_index(db, root):
    root = Path(root).resolve()
    known = {row["path"]: (row["mtime_ns"], row["size"]) for row in db.execute("SELECT * FROM files")}
    seen = set()
    changed = 0
    for path in documentation_files(root):
        relative = path.relative_to(root).as_posix()
        seen.add(relative)
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if known.get(relative) == signature:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        with db:
            db.execute("DELETE FROM chunks WHERE path = ?", (relative,))
            db.executemany(
                "INSERT INTO chunks(path, line, heading, body) VALUES (?, ?, ?, ?)",
                ((relative, line, heading, body) for line, heading, body in sections(content)),
            )
            db.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?)", (relative, *signature))
        changed += 1
    for relative in known.keys() - seen:
        with db:
            db.execute("DELETE FROM chunks WHERE path = ?", (relative,))
            db.execute("DELETE FROM files WHERE path = ?", (relative,))
        changed += 1
    return len(seen), changed


def _matches(db, terms, limit):
    expression = " OR ".join('"%s"' % term for term in terms)
    return db.execute(
        "SELECT path, line, heading, body, bm25(chunks, 2, 0, 4, 1) AS rank "
        "FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
        (expression, limit),
    ).fetchall()


def excerpt(body, first_line, terms, focus=None, max_lines=8):
    lines = body.splitlines()
    if not lines:
        return int(first_line), ""
    eligible = [offset for offset, line in enumerate(lines)
                if line.strip() and not line.lstrip().startswith(("```", "~~~"))]
    best = (focus if focus is not None else
            max(eligible or [0], key=lambda offset: sum(term in lines[offset].lower() for term in terms)))
    block = None
    opening = None
    for offset, text in enumerate(lines):
        if text.lstrip().startswith(("```", "~~~")):
            if opening is None:
                opening = offset + 1
            else:
                if opening <= best < offset:
                    block = (opening, offset)
                opening = None
    if block is None and opening is not None and opening <= best:
        block = (opening, len(lines))
    if block:
        start, end = block
        while start < end and not lines[start].strip():
            start += 1
        while end > start and not lines[end - 1].strip():
            end -= 1
        if end - start > max_lines:
            start = best
            while start > block[0] and lines[start - 1].rstrip().endswith("\\"):
                start -= 1
            end = start
            while end < block[1] and lines[end].strip() and end - start < max_lines:
                end += 1
    else:
        start = end = best
        while start > 0 and lines[start - 1].strip():
            start -= 1
        while end + 1 < len(lines) and lines[end + 1].strip():
            end += 1
        end += 1
        if end - start > max_lines:
            start = max(start, min(best - 2, end - max_lines))
            end = start + max_lines
    selected = "\n".join(line.rstrip()[:220] for line in lines[start:end])
    return int(first_line) + start, selected


def search(db, query, limit=10):
    words = [word.lower() for word in WORD.findall(query)]
    terms = list(dict.fromkeys(word for word in words if len(word) > 1 and word not in STOPWORDS))
    if not terms:
        return []
    groups = [[word, *ALIASES.get(word, ())] for word in terms]
    missing = [index for index, group in enumerate(groups) if not _matches(db, group, 1)]
    if missing:
        vocab = [row[0] for row in db.execute("SELECT term FROM vocabulary")]
        for index in missing:
            correction = difflib.get_close_matches(terms[index], vocab, n=1, cutoff=0.7)
            if correction:
                groups[index].append(correction[0])
    expanded = list(dict.fromkeys(term for group in groups for term in group))
    rows = _matches(db, expanded, max(100, limit * 10))
    def order(row):
        content = " ".join((row["path"], row["heading"], row["body"])).lower()
        coverage = sum(any(alias in content for alias in group) for group in groups)
        return (-coverage, row["rank"])
    rows = sorted(rows, key=order)[:limit]
    results = []
    for row in rows:
        line, snippet = excerpt(row["body"], row["line"], expanded)
        results.append({"path": row["path"], "line": line, "heading": row["heading"], "excerpt": snippet, "score": round(-row["rank"], 6)})
    return results
