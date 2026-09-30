"""Local semantic graph indexing and query-time graph retrieval."""

import difflib
import json
import math
import re
from collections import defaultdict

from .search import STOPWORDS, excerpt


TOKEN = re.compile(r"[\w]+", re.UNICODE)
GRAPH_VERSION = 1
QUERY_STOPWORDS = STOPWORDS | {"against"}
QUERY_ALIASES = {
    "evaluation": ("eval", "evaluate", "evaluator"),
    "evaluate": ("eval", "evaluation"),
    "eval": ("evaluate", "evaluation"),
}


def _cosine(left, right):
    return sum(a * b for a, b in zip(left, right))


def _normalize(vector):
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else list(vector)


def _tokens(text):
    return [(match.group().lower(), match.start(), match.end()) for match in TOKEN.finditer(text)]


def _query_terms(query, vocabulary):
    terms = []
    for term, _, _ in _tokens(query):
        if len(term) <= 1 or term in QUERY_STOPWORDS:
            continue
        if term not in vocabulary and len(term) >= 5:
            correction = difflib.get_close_matches(term, vocabulary, n=1, cutoff=0.8)
            if correction:
                term = correction[0]
        if term not in terms:
            terms.append(term)
    return terms


def _code_lines(body):
    fenced = False
    for offset, line in enumerate(body.splitlines()):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif fenced and line.strip() and not line.lstrip().startswith("#"):
            yield offset, line


def _command_evidence(code, terms, surfaces):
    words = [token for token, _, _ in _tokens(code)]
    matches = sum(bool(set(words) & aliases) for aliases in surfaces)
    direct = 0
    if "run" in words:
        position = words.index("run") + 1
        if position < len(words):
            command = words[position]
            direct = int(any(command in aliases for term, aliases in zip(terms, surfaces)
                             if term not in ("run", "execute", "launch")))
    return matches + 2 * direct


def _tables(db):
    db.execute("CREATE TABLE IF NOT EXISTS semantic_meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS semantic_chunks (id INTEGER PRIMARY KEY, path TEXT, line INTEGER, heading TEXT, body TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS semantic_nodes (id INTEGER PRIMARY KEY, token TEXT, vector TEXT, occurrences INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS semantic_edges (chunk_id INTEGER, node_id INTEGER, frequency INTEGER, PRIMARY KEY (chunk_id, node_id))")
    db.execute("CREATE INDEX IF NOT EXISTS semantic_nodes_token ON semantic_nodes(token)")
    db.execute("CREATE INDEX IF NOT EXISTS semantic_edges_node ON semantic_edges(node_id)")


def graph_ready(db, model_id):
    _tables(db)
    meta = dict(db.execute("SELECT key, value FROM semantic_meta"))
    files = sorted((row[0], row[1], row[2]) for row in db.execute("SELECT path, mtime_ns, size FROM files"))
    return (meta.get("version") == str(GRAPH_VERSION)
            and meta.get("model") == model_id
            and meta.get("files") == json.dumps(files))


def build_graph(db, encoder):
    """Rebuild the graph when source files or the local encoder change.

    encoder.encode(text, spans) returns one contextual vector for each span.
    """
    if graph_ready(db, encoder.model_id):
        return False
    files = sorted((row[0], row[1], row[2]) for row in db.execute("SELECT path, mtime_ns, size FROM files"))
    chunks = db.execute("SELECT path, line, heading, body FROM chunks ORDER BY path, line").fetchall()
    # Build everything before replacing the stored graph, so encoder failures
    # leave the previous graph intact.
    nodes = []
    by_token = defaultdict(list)
    graph_chunks = []
    edges = []
    for chunk_id, row in enumerate(chunks, 1):
        path, line, heading, body = row
        text = heading + "\n" + body
        spans = [span for span in _tokens(text) if len(span[0]) > 1 and span[0] not in STOPWORDS]
        vectors = encoder.encode(text, [(start, end) for _, start, end in spans])
        if len(vectors) != len(spans):
            raise ValueError("semantic encoder returned the wrong number of token vectors")
        graph_chunks.append((chunk_id, path, line, heading, body))
        counts = defaultdict(int)
        for (token, _, _), vector in zip(spans, vectors):
            vector = _normalize(vector)
            choices = by_token[token]
            closest = max(choices, key=lambda index: _cosine(vector, _normalize(nodes[index][1])), default=None)
            if closest is None or _cosine(vector, _normalize(nodes[closest][1])) < 0.8:
                closest = len(nodes)
                nodes.append([token, vector, 0])
                choices.append(closest)
            else:
                node = nodes[closest]
                node[1] = [node[1][i] + value for i, value in enumerate(vector)]
            nodes[closest][2] += 1
            counts[closest + 1] += 1
        edges.extend((chunk_id, node_id, frequency) for node_id, frequency in counts.items())
    with db:
        db.execute("DELETE FROM semantic_edges")
        db.execute("DELETE FROM semantic_nodes")
        db.execute("DELETE FROM semantic_chunks")
        db.executemany("INSERT INTO semantic_chunks VALUES (?, ?, ?, ?, ?)", graph_chunks)
        db.executemany("INSERT INTO semantic_nodes VALUES (?, ?, ?, ?)",
                       ((index, token, json.dumps(_normalize(vector)), count)
                        for index, (token, vector, count) in enumerate(nodes, 1)))
        db.executemany("INSERT INTO semantic_edges VALUES (?, ?, ?)", edges)
        db.execute("DELETE FROM semantic_meta")
        db.executemany("INSERT INTO semantic_meta VALUES (?, ?)",
                       (("version", str(GRAPH_VERSION)), ("model", encoder.model_id),
                        ("files", json.dumps(files))))
    return True


def semantic_search(db, query, encoder, limit=10):
    """Build a query-specific semantic co-occurrence graph and rank chunks."""
    if not graph_ready(db, encoder.model_id):
        raise ValueError("semantic graph is missing or stale; run index --semantic first")
    nodes = {row[0]: (row[1], json.loads(row[2]))
             for row in db.execute("SELECT id, token, vector FROM semantic_nodes")}
    if not nodes:
        return []
    terms = _query_terms(query, {surface for surface, _ in nodes.values()})
    if not terms:
        return []
    normalized_query = " ".join(terms)
    spans = _tokens(normalized_query)
    vectors = encoder.encode(normalized_query, [(start, end) for _, start, end in spans])
    if len(vectors) != len(spans):
        raise ValueError("semantic encoder returned the wrong number of query vectors")
    chunk_nodes = defaultdict(dict)
    node_chunks = defaultdict(set)
    for chunk_id, node_id, frequency in db.execute("SELECT chunk_id, node_id, frequency FROM semantic_edges"):
        chunk_nodes[chunk_id][node_id] = frequency
        node_chunks[node_id].add(chunk_id)
    groups = []
    surfaces = []
    for term, vector in zip(terms, vectors):
        vector = _normalize(vector)
        aliases = {term, *QUERY_ALIASES.get(term, ())}
        matched = {}
        semantic = []
        for node_id, (surface, anchor) in nodes.items():
            similarity = _cosine(vector, anchor)
            if surface == term:
                matched[node_id] = 3.0 * (0.5 + 0.5 * max(similarity, 0))
            elif surface in aliases:
                matched[node_id] = 2.5 * (0.5 + 0.5 * max(similarity, 0))
            elif len(term) >= 4 and surface.startswith(term):
                matched[node_id] = 1.5 * (0.5 + 0.5 * max(similarity, 0))
            elif similarity >= 0.75:
                semantic.append((similarity, node_id))
        for similarity, node_id in sorted(semantic, reverse=True)[:3]:
            matched[node_id] = similarity
        groups.append(matched)
        surfaces.append(aliases)
    if not any(groups):
        return []

    # Connect meanings selected by different query concepts. Multiple senses
    # of one word must not reinforce one another or reward repetition.
    connected = defaultdict(float)
    for position, left_group in enumerate(groups):
        for right_group in groups[position + 1:]:
            for left in left_group:
                for right in right_group:
                    overlap = len(node_chunks[left] & node_chunks[right])
                    if overlap:
                        weight = overlap / math.sqrt(len(node_chunks[left]) * len(node_chunks[right]))
                        connected[left] += weight
                        connected[right] += weight
    total_chunks = len(chunk_nodes)
    ranked_chunks = []
    procedural = any(term in ("run", "execute", "launch") for term in terms)
    for row in db.execute("SELECT id, path, line, heading, body FROM semantic_chunks"):
        chunk_id, path, line, heading, body = row
        present = chunk_nodes[chunk_id]
        score = 0.0
        coverage = 0
        for group, exact_surfaces in zip(groups, surfaces):
            best = 0.0
            found_exact = False
            for node_id, confidence in group.items():
                if node_id not in present:
                    continue
                idf = math.log1p(total_chunks / len(node_chunks[node_id]))
                structure = 1.0 + min(connected[node_id], 2.0)
                frequency = present[node_id]
                best = max(best, confidence * structure * idf * frequency / (frequency + 1))
                found_exact |= nodes[node_id][0] in exact_surfaces
            score += best
            coverage += found_exact
        command_matches = 0
        command_offset = None
        if procedural:
            for offset, code in _code_lines(body):
                matches = _command_evidence(code, terms, surfaces)
                if matches > command_matches:
                    command_matches, command_offset = matches, offset
        score += 4 * coverage + 10 * command_matches
        if score:
            ranked_chunks.append((score, path, line, heading, body, command_offset))
    ranked_chunks.sort(key=lambda item: (-item[0], item[1], item[2]))
    results = []
    for score, path, line, heading, body, command_offset in ranked_chunks[:limit]:
        source_line, snippet = excerpt(body, line, set().union(*surfaces), focus=command_offset)
        results.append({"path": path, "line": source_line, "heading": heading,
                        "excerpt": snippet, "score": round(score, 6)})
    return results
