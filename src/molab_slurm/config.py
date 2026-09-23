"""Which box to talk to: named boxes in ~/.config/molab/config.json (mode 600).

    molab init https://sb-....molab.run/ TOKEN --name gpu   # saved, and the default
    molab --box cpu squeue                                  # any other saved box

A molab session's URL changes every time the session is recreated, so
`molab init` is run once per session; the name is what scripts refer to.
MOLAB_URL + MOLAB_TOKEN in the environment override the file entirely, and
MOLAB_BOX picks a saved box, for one-off use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "molab" / "config.json"

DEFAULTS = {
    "cpus": 4,  # the real slice; molab boxes report the host's count
    "workdir": "/marimo",  # where sbatch runs when no --chdir/#SBATCH -D is given
    "root": "/marimo/.molab",  # job state on the box
}


class ConfigError(Exception):
    pass


def load() -> dict:
    if not CONFIG.exists():
        return {"default": None, "boxes": {}}
    try:
        cfg = json.loads(CONFIG.read_text())
    except ValueError as e:
        raise ConfigError(f"{CONFIG} is not valid JSON: {e}") from e
    cfg.setdefault("boxes", {})
    cfg.setdefault("default", None)
    return cfg


def save(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.chmod(0o600)  # it holds tokens: code execution on the box
    tmp.replace(CONFIG)


def add_box(name: str, url: str, token: str, *, make_default: bool = True, **settings) -> dict:
    cfg = load()
    box = {**DEFAULTS, **(cfg["boxes"].get(name) or {}), "url": url, "token": token}
    box.update({k: v for k, v in settings.items() if v is not None})
    cfg["boxes"][name] = box
    if make_default or not cfg["default"]:
        cfg["default"] = name
    save(cfg)
    return box


def resolve(name: str | None = None) -> tuple[str, dict]:
    """(name, box settings) for --box NAME, else MOLAB_URL/MOLAB_TOKEN, else MOLAB_BOX, else the default."""
    if name is None and os.environ.get("MOLAB_URL"):
        return "env", {**DEFAULTS, "url": os.environ["MOLAB_URL"], "token": os.environ.get("MOLAB_TOKEN", "")}
    cfg = load()
    name = name or os.environ.get("MOLAB_BOX") or cfg["default"]
    if not name:
        raise ConfigError("no box configured: run `molab init <notebook-url> <token>` first")
    if name not in cfg["boxes"]:
        known = ", ".join(sorted(cfg["boxes"])) or "none"
        raise ConfigError(f"no box named {name!r} (known: {known})")
    return name, {**DEFAULTS, **cfg["boxes"][name]}
