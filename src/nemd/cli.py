"""Command-line interface for documentation search."""

import argparse
import json
import os
import re
import select
import shutil
import sqlite3
import sys
import termios
import time
import tty
from pathlib import Path

from .config import get_semantic_model, set_semantic_model, unset_semantic_model
from .search import default_index_path, open_index, refresh_index, search
from .semantic import build_graph, semantic_search

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HEADING = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
QUOTE = re.compile(r"^ {0,3}>")
LIST_ITEM = re.compile(r"^\s*(?:[-*+] |\d+[.)] )")
INLINE_CODE = re.compile(r"(`+)([^`]+)\1")


def _markdown_kinds(lines):
    kinds = []
    fence = None
    for line in lines:
        marker = FENCE.match(line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) \
                    and not line[marker.end():].strip():
                fence = None
                kinds.append("fence")
            else:
                kinds.append("code")
        elif marker:
            fence = marker.group(1)
            kinds.append("fence")
        elif HEADING.match(line):
            kinds.append("heading")
        elif QUOTE.match(line):
            kinds.append("quote")
        elif LIST_ITEM.match(line):
            kinds.append("list")
        else:
            kinds.append("prose")
    return kinds


def _highlight_markdown(text, kind, color):
    if not color:
        return text
    style = {"heading": "1;36", "fence": "2", "code": "32",
             "quote": "3;32", "list": "33"}.get(kind)
    if style:
        return "\x1b[{}m{}\x1b[0m".format(style, text)
    return INLINE_CODE.sub(lambda match: "\x1b[32m{}\x1b[0m".format(match.group()), text)


def _print_results(results, root):
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
        header = location + (" " + heading if heading else "")
        if color:
            target = (root / hit["path"]).resolve().as_uri() + "#L{}".format(hit["line"])
            header = "\x1b]8;;{}\x1b\\\x1b[1;36m{}\x1b[0m\x1b]8;;\x1b\\".format(target, header)
        print("{}\n{}".format(header,
                               "\n".join("  " + line for line in (snippet or [""]))))


def _read_key(fd):
    key = os.read(fd, 1)
    if key == b"\x1b":
        if not select.select([fd], [], [], 0.1)[0]:
            return "cancel"
        prefix = os.read(fd, 1)
        if prefix not in (b"[", b"O"):
            return "cancel"
        sequence = b""
        while select.select([fd], [], [], 0.1)[0] and len(sequence) < 32:
            sequence += os.read(fd, 1)
            if sequence[-1:] in (b"A", b"B", b"Z", b"~", b"M", b"m", b"R"):
                break
        if sequence.startswith(b"<") and sequence[-1:] in (b"M", b"m"):
            try:
                button, x, y = (int(part) for part in sequence[1:-1].split(b";"))
            except ValueError:
                return "ignore"
            if button & 64:
                return "up" if button & 1 == 0 else "down"
            if sequence.endswith(b"M") and button & 3 == 0:
                return ("click", x, y)
        return {b"A": "up", b"B": "down", b"Z": "up",
                b"5~": "page_up", b"6~": "page_down"}.get(sequence, "ignore")
    return {b"\t": "down", b"\r": "choose", b"\n": "choose",
            b"\x7f": "back", b"\x08": "back", b"q": "cancel",
            b"\x03": "cancel", b"\x04": "cancel"}.get(key, "ignore")


def _cursor_row(fd, output):
    output.write("\x1b[6n")
    output.flush()
    reply = b""
    deadline = time.monotonic() + 0.15
    while time.monotonic() < deadline and len(reply) < 32:
        if not select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
            break
        reply += os.read(fd, 1)
        if reply.endswith(b"R"):
            try:
                return int(reply.split(b"[")[1].split(b";")[0])
            except (IndexError, ValueError):
                break
    return None


def _pick_result(results, read_key, output, width, height, color=False, cursor_row=None):
    page_size = min(len(results), 7, max(1, height - 4))
    selected = 0
    top = 0
    rendered = False
    prompt_row = None
    queried_row = False
    while True:
        if selected < top:
            top = selected
        elif selected >= top + page_size:
            top = selected - page_size + 1
        lines = ["? Choose a result ({}/{})".format(selected + 1, len(results))]
        for index in range(top, min(len(results), top + page_size)):
            hit = results[index]
            preview = next((line.strip() for line in hit["excerpt"].splitlines() if line.strip()), "")
            label = "{}:{}".format(hit["path"], hit["line"])
            if hit["heading"]:
                label += " [{}]".format(hit["heading"])
            prefix = "> " if index == selected else "  "
            clean = "".join(char if char.isprintable() else " " for char in prefix + label + " - " + preview)
            clean = clean[:max(1, width - 1)]
            if color:
                label_end = min(len(prefix + label), len(clean))
                clean = clean[:len(prefix)] + "\x1b[1;36m" + clean[len(prefix):label_end] \
                    + "\x1b[0m" + clean[label_end:]
            lines.append(clean)
        lines.extend(["" for _ in range(page_size - len(lines) + 1)])
        lines.append("Up/Down or Tab: move  Enter: select  Esc: cancel")
        lines[0] = lines[0][:max(1, width - 1)]
        lines[-1] = lines[-1][:max(1, width - 1)]
        if rendered:
            output.write("\r\x1b[{}A".format(len(lines) - 1))
        output.write("\r\x1b[2K" + "\n\r\x1b[2K".join(lines))
        output.flush()
        rendered = True
        if cursor_row is not None and not queried_row:
            end_row = cursor_row()
            prompt_row = end_row - len(lines) + 1 if end_row is not None else None
            queried_row = True
        key = read_key()
        if key == "down":
            selected = (selected + 1) % len(results)
        elif key == "up":
            selected = (selected - 1) % len(results)
        elif key == "choose":
            output.write("\r\n")
            output.flush()
            return results[selected]
        elif isinstance(key, tuple) and key[0] == "click" and prompt_row is not None:
            row = key[2] - prompt_row
            clicked = top + row - 1
            if 1 <= row <= page_size and clicked < len(results):
                output.write("\r\n")
                output.flush()
                return results[clicked]
        elif key == "cancel":
            output.write("\r\n")
            output.flush()
            return None


def _view_result(hit, root, read_key, output, width, height, color=False):
    try:
        source = (root / hit["path"]).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        source = ["Could not read source: %s" % error]
    kinds = _markdown_kinds(source)
    for text, kind in zip(source, kinds):
        clean = "".join(char if char.isprintable() else " " for char in text)
        output.write(_highlight_markdown(clean, kind, color) + "\r\n")
    output.flush()
    page_size = max(1, height - 2)
    match = min(max(0, hit["line"] - 1), len(source) - 1)
    top = min(max(0, match - 2), max(0, len(source) - page_size))
    header = "{}:{}".format(hit["path"], hit["line"])
    if hit["heading"]:
        header += " [{}]".format(hit["heading"])
    rendered = False
    while True:
        clean_header = "".join(char if char.isprintable() else " " for char in header)[:max(1, width - 1)]
        if color:
            clean_header = "\x1b[1;36m{}\x1b[0m".format(clean_header)
        lines = [clean_header]
        for offset in range(page_size):
            index = top + offset
            if index >= len(source):
                lines.append("")
                continue
            prefix = "> " if index == match else "  "
            clean = "".join(char if char.isprintable() else " " for char in source[index])
            clean = clean[:max(0, width - len(prefix) - 1)]
            lines.append(prefix + _highlight_markdown(clean, kinds[index], color))
        lines.append("Up/Down: scroll  PgUp/PgDn: page  Backspace: results  Enter/Esc: close"[:max(1, width - 1)])
        if rendered:
            output.write("\r\x1b[{}A".format(len(lines) - 1))
        output.write("\r\x1b[2K" + "\n\r\x1b[2K".join(lines))
        output.flush()
        rendered = True
        key = read_key()
        if key == "down":
            top = min(top + 1, max(0, len(source) - page_size))
        elif key == "up":
            top = max(0, top - 1)
        elif key == "page_down":
            top = min(top + page_size, max(0, len(source) - page_size))
        elif key == "page_up":
            top = max(0, top - page_size)
        elif key in ("back", "cancel", "choose"):
            output.write("\r\x1b[2K\r\n")
            output.flush()
            return key


def _choose_result(results, root):
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l\x1b[?1000h\x1b[?1006h")
        size = shutil.get_terminal_size((80, 24))
        width, height = size.columns or 80, size.lines or 24
        color = not os.environ.get("NO_COLOR")
        read_key = lambda: _read_key(fd)
        while True:
            selected = _pick_result(results, read_key, sys.stdout, width, height, color,
                                    lambda: _cursor_row(fd, sys.stdout))
            if selected is None:
                return None
            if _view_result(selected, root, read_key, sys.stdout, width, height, color) != "back":
                return selected
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
        sys.stdout.write("\x1b[?1006l\x1b[?1000l\x1b[?25h")
        sys.stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nemd", description="Search documentation in a local project")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "search"):
        command = subcommands.add_parser(name, aliases=["s"] if name == "search" else [])
        command.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default: current directory)")
        command.add_argument("--index", type=Path, help="index file (default: user cache)")
        command.add_argument("--semantic", action="store_true", help="use the local semantic graph")
        command.add_argument("--model", type=Path, help="local contextual model directory (or NEMD_SEMANTIC_MODEL)")
        if name == "search":
            command.add_argument("query", help="words or a natural-language question")
            command.add_argument("--limit", type=int, default=10)
            command.add_argument("--json", action="store_true", help="machine-readable results")
    config = subcommands.add_parser("config", help="manage saved settings")
    actions = config.add_subparsers(dest="config_action", required=True)
    for action in ("get", "set", "unset"):
        command = actions.add_parser(action)
        command.add_argument("key", choices=["semantic-model", "NEMD_SEMANTIC_MODEL"])
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
            print("nemd: %s" % error, file=sys.stderr)
            return 2
        return 0
    root = args.root.resolve()
    if not root.is_dir():
        parser.error("project root is not a directory: %s" % root)
    if args.command in ("search", "s") and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        model = (args.model or os.environ.get("NEMD_SEMANTIC_MODEL") or get_semantic_model()) if args.semantic else None
        if args.semantic and not model:
            parser.error("--semantic requires --model, NEMD_SEMANTIC_MODEL, or a saved semantic-model setting")
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
        print("nemd: %s" % error, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(results, indent=2))
    elif not results:
        print("No matching documentation found.")
    elif sys.stdout.isatty() and sys.stdin.isatty() and os.environ.get("TERM") != "dumb":
        _choose_result(results, root)
    else:
        _print_results(results, root)
    return 0
