"""SLURM semantics as pure functions: no network, no box, fully unit-tested.

Everything here answers "what would SLURM do with this?" -- how `#SBATCH`
lines are read, how an array spec expands, how `%A_%a` fills in, how a
walltime parses, how squeue collapses pending tasks. The CLI builds a job
spec from these and ships it to the box; the box-side runner never parses
SLURM syntax itself.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

# Options molab honours. Everything else a script asks for is accepted,
# recorded and reported as ignored: there is no scheduler to enforce it.
HONOURED = {
    "array",
    "job-name",
    "output",
    "error",
    "chdir",
    "cpus-per-task",
    "time",
    "dependency",
    "export",
}
SHORT = {"-a": "array", "-J": "job-name", "-o": "output", "-e": "error", "-D": "chdir",
         "-c": "cpus-per-task", "-t": "time", "-d": "dependency"}  # fmt: skip
FLAGS = {"exclusive", "requeue", "no-requeue", "parsable", "hold", "test-only", "wait"}


@dataclass
class ArraySpec:
    indices: list[int]
    throttle: int | None = None  # the %N suffix: at most N tasks at once

    def compact(self, subset: list[int] | None = None) -> str:
        """`5-9%1`-style text, SLURM's squeue notation for a set of indices."""
        idx = sorted(self.indices if subset is None else subset)
        parts, i = [], 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
                j += 1
            parts.append(str(idx[i]) if i == j else f"{idx[i]}-{idx[j]}")
            i = j + 1
        text = ",".join(parts)
        return text + (f"%{self.throttle}" if self.throttle else "")


def parse_array(spec: str) -> ArraySpec:
    """`0-3`, `0,2,4`, `1-9:2`, `0-19%4` -> ArraySpec. Raises ValueError like sbatch would."""
    spec = spec.strip()
    throttle = None
    if "%" in spec:
        spec, t = spec.split("%", 1)
        throttle = int(t)
        if throttle < 1:
            raise ValueError(f"array throttle must be >= 1, got %{t}")
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty element in --array={spec}")
        step = 1
        if ":" in part:
            part, s = part.split(":", 1)
            step = int(s)
            if step < 1:
                raise ValueError(f"array step must be >= 1 in {part}:{s}")
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            if hi < lo:
                raise ValueError(f"array range {lo}-{hi} runs backwards")
            out.extend(range(lo, hi + 1, step))
        else:
            out.append(int(part))
    if any(i < 0 for i in out):
        raise ValueError("array indices must be >= 0")
    return ArraySpec(sorted(set(out)), throttle)


def parse_time(text: str) -> int | None:
    """SLURM walltime -> seconds. `MM`, `MM:SS`, `HH:MM:SS`, `D-HH`, `D-HH:MM`,
    `D-HH:MM:SS`; `UNLIMITED`/`infinite` -> None (no limit)."""
    t = text.strip()
    if t.lower() in ("unlimited", "infinite", ""):
        return None
    days = 0
    if "-" in t:
        d, t = t.split("-", 1)
        days = int(d)
        parts = [int(x) for x in t.split(":")]
        h, m, s = (parts + [0, 0])[:3]  # D-HH, D-HH:MM, D-HH:MM:SS
    else:
        parts = [int(x) for x in t.split(":")]
        if len(parts) == 1:
            h, m, s = 0, parts[0], 0  # MM
        elif len(parts) == 2:
            h, m, s = 0, parts[0], parts[1]  # MM:SS
        elif len(parts) == 3:
            h, m, s = parts
        else:
            raise ValueError(f"cannot parse --time={text}")
    return ((days * 24 + h) * 60 + m) * 60 + s


def format_elapsed(seconds: float | None) -> str:
    """squeue's TIME column: `M:SS`, `H:MM:SS`, `D-HH:MM:SS`."""
    if seconds is None:
        return "0:00"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}-{h:02d}:{m:02d}:{s:02d}"
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_dependency(text: str) -> list[tuple[str, str]]:
    """`afterok:12:13,afterany:9` -> [("afterok","12"), ("afterok","13"), ("afterany","9")].

    Supported types: after, afterany, afterok, afternotok. `?` (OR) is not.
    """
    if "?" in text:
        raise ValueError("OR dependencies (`?`) are not supported; use `,` (AND)")
    out = []
    for clause in filter(None, text.split(",")):
        kind, *ids = clause.split(":")
        if kind not in ("after", "afterany", "afterok", "afternotok"):
            raise ValueError(f"unsupported dependency type {kind!r}")
        if not ids:
            raise ValueError(f"dependency {clause!r} names no job")
        out.extend((kind, i) for i in ids)
    return out


def _normalise(opt: str) -> tuple[str, str | None]:
    """`--job-name=x` / `--job-name` / `-J` -> ("job-name", "x" | None)."""
    if opt.startswith("--"):
        name, _, val = opt[2:].partition("=")
        return name, (val if _ else None)
    if opt[:2] in SHORT:
        rest = opt[2:]
        return SHORT[opt[:2]], (rest.lstrip("=") if rest else None)
    raise ValueError(f"unrecognised option {opt!r}")


def parse_options(tokens: list[str]) -> dict[str, str]:
    """sbatch-style option tokens -> {long-name: value}. Flags map to ""."""
    opts: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith("-"):
            raise ValueError(f"expected an option, got {tok!r}")
        name, val = _normalise(tok)
        if val is None and name not in FLAGS:
            if i + 1 >= len(tokens):
                raise ValueError(f"option --{name} needs a value")
            val = tokens[i + 1]
            i += 1
        opts[name] = "" if val is None else val
        i += 1
    return opts


def parse_directives(script_text: str) -> dict[str, str]:
    """Read `#SBATCH` lines the way sbatch does: only in the leading comment
    block, stopping at the first line of code; later lines win."""
    tokens: list[str] = []
    for n, line in enumerate(script_text.splitlines()):
        s = line.strip()
        if n == 0 and s.startswith("#!"):
            continue
        if not s or (s.startswith("#") and not s.startswith("#SBATCH")):
            continue  # blank lines and ordinary comments do not end the block
        if not s.startswith("#SBATCH"):
            break  # first command: sbatch stops reading directives here
        body = s[len("#SBATCH") :].split(" #", 1)[0]  # trailing comment
        tokens.extend(shlex.split(body))
    return parse_options(tokens)


@dataclass
class JobOptions:
    """The merged view: script directives, overridden by command-line flags."""

    array: ArraySpec | None = None
    name: str | None = None
    output: str | None = None
    error: str | None = None
    chdir: str | None = None
    cpus: int | None = None
    time_limit: int | None = None
    dependency: list[tuple[str, str]] = field(default_factory=list)
    export: str = "ALL"
    ignored: dict[str, str] = field(default_factory=dict)


def merge(directives: dict[str, str], cli: dict[str, str]) -> JobOptions:
    """Command line beats `#SBATCH` (SLURM's rule); unknown options are kept as ignored."""
    merged = {**directives, **cli}
    o = JobOptions()
    for k, v in merged.items():
        if k == "array":
            o.array = parse_array(v)
        elif k == "job-name":
            o.name = v
        elif k == "output":
            o.output = v
        elif k == "error":
            o.error = v
        elif k == "chdir":
            o.chdir = v
        elif k == "cpus-per-task":
            o.cpus = int(v)
        elif k == "time":
            o.time_limit = parse_time(v)
        elif k == "dependency":
            o.dependency = parse_dependency(v)
        elif k == "export":
            o.export = v
        else:
            o.ignored[k] = v
    return o


# One implementation, shared with the box: the runner fills output paths when a
# task starts (only it knows the job id, host and user), and it must be
# standalone there, so the function lives in runner.py and is re-exported here.
from molab_slurm.runner import fill_pattern  # noqa: E402,F401


def default_output(is_array: bool) -> str:
    return "slurm-%A_%a.out" if is_array else "slurm-%j.out"


def export_env(spec: str) -> tuple[bool, dict[str, str]]:
    """`--export` -> (inherit the box environment?, extra variables).

    ALL (default) inherits; NONE starts from a minimal environment; either
    may be followed by `,VAR=val`; a bare `VAR=val,...` implies ALL here
    (SLURM would imply NONE, which on a box nobody logs into is never wanted).
    """
    parts = [p for p in spec.split(",") if p]
    inherit, extra = True, {}
    for p in parts:
        if p.upper() == "ALL":
            inherit = True
        elif p.upper() == "NONE":
            inherit = False
        elif "=" in p:
            k, v = p.split("=", 1)
            extra[k] = v
        else:
            raise ValueError(f"--export: {p!r} must be ALL, NONE or VAR=value")
    return inherit, extra
