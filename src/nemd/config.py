"""Persistent user preferences for the CLI."""

import json
import os
import tempfile
from pathlib import Path


def config_path():
    home = os.environ.get("XDG_CONFIG_HOME")
    return (Path(home).expanduser() if home else Path.home() / ".config") / "nemd" / "config.json"


def _read():
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("invalid config file: %s" % path) from error
    if not isinstance(data, dict):
        raise ValueError("invalid config file: %s" % path)
    return data


def _write(data):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def get_semantic_model():
    value = _read().get("semantic_model")
    if value is not None and not isinstance(value, str):
        raise ValueError("semantic_model must be a path in %s" % config_path())
    return value


def set_semantic_model(value):
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("semantic model directory does not exist: %s" % path)
    data = _read()
    data["semantic_model"] = str(path)
    _write(data)
    return path


def unset_semantic_model():
    data = _read()
    if data.pop("semantic_model", None) is not None:
        _write(data)
