"""Command-line interface for documentation search."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .config import get_semantic_model, set_semantic_model, unset_semantic_model
from .search import default_index_path, open_index, refresh_index, search
from .semantic import build_graph, semantic_search


def _print_results(results):
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    for index, hit in enumerate(results):
        if index:
            print()
        location = "{}:{}".format(hit["path"], hit["line"])
        heading = "[{}]".format(hit["heading"]) if hit["heading"] else ""
        snippet = ["".join(char if char.isprintable() else " " for char in line)
                   for line in hit["excerpt"].splitlines()]
        location, heading = ("".join(char if char.isprintable() else " " for char in value)
                             for value in (location, heading))
        if color:
            location = "\x1b[1;36m{}\x1b[0m".format(location)
            heading = "\x1b[2m{}\x1b[0m".format(heading) if heading else ""
        print("{}{}\n{}".format(location, " " + heading if heading else "",
                               "\n".join("  " + line for line in (snippet or [""]))))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="urag", description="Search documentation in a local project")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "fetch"):
        command = subcommands.add_parser(name)
        command.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default: current directory)")
        command.add_argument("--index", type=Path, help="index file (default: user cache)")
        command.add_argument("--semantic", action="store_true", help="use the local semantic graph")
        command.add_argument("--model", type=Path, help="local contextual model directory (or URAG_SEMANTIC_MODEL)")
        if name == "fetch":
            command.add_argument("query", help="words or a natural-language question")
            command.add_argument("--limit", type=int, default=10)
            command.add_argument("--json", action="store_true", help="machine-readable results")
    config = subcommands.add_parser("config", help="manage saved settings")
    actions = config.add_subparsers(dest="config_action", required=True)
    for action in ("get", "set", "unset"):
        command = actions.add_parser(action)
        command.add_argument("key", choices=["semantic-model", "URAG_SEMANTIC_MODEL"])
        if action == "set":
            command.add_argument("value", help="local semantic model directory")
    args = parser.parse_args(argv)
    if args.command == "config":
        try:
            if args.config_action == "set":
                print("Saved semantic model: %s" % set_semantic_model(args.value))
            elif args.config_action == "unset":
                unset_semantic_model()
                print("Semantic model unset.")
            else:
                value = get_semantic_model()
                if not value:
                    print("Semantic model is not set.", file=sys.stderr)
                    return 1
                print(value)
        except (OSError, ValueError) as error:
            print("urag: %s" % error, file=sys.stderr)
            return 2
        return 0
    root = args.root.resolve()
    if not root.is_dir():
        parser.error("project root is not a directory: %s" % root)
    if args.command == "fetch" and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        model = (args.model or os.environ.get("URAG_SEMANTIC_MODEL") or get_semantic_model()) if args.semantic else None
        if args.semantic and not model:
            parser.error("--semantic requires --model, URAG_SEMANTIC_MODEL, or a saved semantic-model setting")
        with open_index(args.index or default_index_path(root)) as db:
            count, changed = refresh_index(db, root)
            if args.semantic:
                from .encoder import LocalTokenEncoder
                encoder = LocalTokenEncoder(model)
                build_graph(db, encoder)
            if args.command == "index":
                suffix = " Semantic graph ready." if args.semantic else ""
                print("Indexed %d document(s); %d changed.%s" % (count, changed, suffix))
                return 0
            results = (semantic_search(db, args.query, encoder, args.limit) if args.semantic
                       else search(db, args.query, args.limit))
    except (OSError, sqlite3.Error, ValueError, ImportError) as error:
        print("urag: %s" % error, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(results, indent=2))
    elif not results:
        print("No matching documentation found.")
    else:
        _print_results(results)
    return 0
