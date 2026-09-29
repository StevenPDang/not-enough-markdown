"""Command-line interface for documentation search."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .search import default_index_path, open_index, refresh_index, search


def _print_results(results):
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    for index, hit in enumerate(results):
        if index:
            print()
        location = "{}:{}".format(hit["path"], hit["line"])
        heading = "[{}]".format(hit["heading"]) if hit["heading"] else ""
        excerpt = hit["excerpt"]
        location, heading, excerpt = ("".join(char if char.isprintable() else " " for char in value)
                                      for value in (location, heading, excerpt))
        if color:
            location = "\x1b[1;36m{}\x1b[0m".format(location)
            heading = "\x1b[2m{}\x1b[0m".format(heading) if heading else ""
        print("{}{}\n  {}".format(location, " " + heading if heading else "", excerpt))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="urag", description="Search documentation in a local project")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "fetch"):
        command = subcommands.add_parser(name)
        command.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default: current directory)")
        command.add_argument("--index", type=Path, help="index file (default: user cache)")
        if name == "fetch":
            command.add_argument("query", help="words or a natural-language question")
            command.add_argument("--limit", type=int, default=10)
            command.add_argument("--json", action="store_true", help="machine-readable results")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        parser.error("project root is not a directory: %s" % root)
    if args.command == "fetch" and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        with open_index(args.index or default_index_path(root)) as db:
            count, changed = refresh_index(db, root)
            if args.command == "index":
                print("Indexed %d document(s); %d changed." % (count, changed))
                return 0
            results = search(db, args.query, args.limit)
    except (OSError, sqlite3.Error) as error:
        print("urag: %s" % error, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(results, indent=2))
    elif not results:
        print("No matching documentation found.")
    else:
        _print_results(results)
    return 0
