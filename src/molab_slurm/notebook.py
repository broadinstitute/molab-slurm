"""Inside a molab notebook: the `molab-slurm init` command that connects this session.

Every new molab session has a new URL and token. Both are known inside the session, just not in one place:
the token is on the marimo server's command line, and the public URL only in the browser -- the kernel sees
molab's internal proxy host, never the address you opened. `init_command()` puts the two together:

    import molab_slurm as mos
    mos.init_command(name="gpu")

shows `molab-slurm init <url> <token> --name gpu`, token hidden, with a copy button. It needs anywidget
(`pip install anywidget`, or the `molab-slurm[notebook]` extra; molab's notebooks already have it). The rest of molab-slurm stays
stdlib-only, and importing this module imports nothing else.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["connect_url", "init_command", "server_token"]

# molab shows a notebook at https://sb-<id>-session.sb.molab.run/session/, a front end that rejects the token;
# the notebook server answers at https://sb-<id>.sb.molab.run/.
_MOLAB_SESSION_HOST = re.compile(r"(sb-[0-9a-z]+)-session\.(.+)")
_HIDDEN = "<token hidden>"


def connect_url(page: str) -> str | None:
    """The notebook server's URL, from the address the browser shows. None unless `page` is an http(s) URL.

    On molab that means dropping the "-session" front end; anywhere else the page's own address, without the
    query, is the server's.
    """
    parts = urlsplit(page or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    port = f":{parts.port}" if parts.port else ""
    m = _MOLAB_SESSION_HOST.fullmatch(parts.hostname)
    if m:
        return f"{parts.scheme}://{m.group(1)}.{m.group(2)}{port}/"
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}/"


def _token_from_argv(argv: list[str], cwd: str | os.PathLike | None = None) -> str | None:
    """The value of marimo's --token-password, or the contents of --token-password-file (relative to `cwd`,
    the server's working directory; "-", stdin, cannot be read back)."""
    for i, arg in enumerate(argv):
        flag, eq, value = arg.partition("=")
        if flag not in ("--token-password", "--token-password-file"):
            continue
        if not eq:
            if i + 1 >= len(argv):
                continue
            value = argv[i + 1]
        if flag == "--token-password":
            return value or None
        if value in ("", "-"):
            continue
        try:
            return (Path(cwd or ".") / value).read_text().strip() or None
        except OSError:
            continue
    return None


def server_token(pid: int | None = None, proc: str | os.PathLike = "/proc") -> str | None:
    """The marimo server's auth token, read from its command line. Linux only; None if it has none.

    `marimo edit` runs the kernel in a child process of the server, `marimo run` in the server itself, so this
    looks at the kernel's own process first and then at its ancestors.
    """
    proc = Path(proc)
    pid = os.getpid() if pid is None else pid
    for _ in range(32):
        try:
            argv = [
                a.decode(errors="replace") for a in (proc / str(pid) / "cmdline").read_bytes().split(b"\0")
            ]
        except OSError:
            return None
        try:
            cwd = os.readlink(proc / str(pid) / "cwd")
        except OSError:
            cwd = None
        token = _token_from_argv([a for a in argv if a], cwd)
        if token:
            return token
        try:
            # the command name in field 2 may contain spaces and parentheses; the fields after the last ")" do not
            ppid = int((proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        if ppid <= 0 or ppid == pid:
            return None
        pid = ppid
    return None


def _command(url: str | None, token: str | None, extra: list[str]) -> tuple[str, str]:
    """(command, command with the token hidden); both "" until the URL is known."""
    if not url:
        return "", ""
    head, tail = shlex.join(["molab-slurm", "init", url]), shlex.join(extra)
    parts = [head, shlex.quote(token) if token else "<token>"] + ([tail] if tail else [])
    shown = [head, _HIDDEN if token else "<token>"] + ([tail] if tail else [])
    return " ".join(parts), " ".join(shown)


_ESM = r"""
function render({ model, el }) {
  // Only the browser knows the address the notebook was opened at. It goes to the kernel, and the full command
  // comes back, as messages rather than widget state: marimo saves widget state into its HTML/PDF exports, and
  // both carry the token (molab's page address embeds it too).
  let command = "";
  let revealed = false;
  const box = document.createElement("div");
  box.className = "molab-init";
  const code = document.createElement("code");
  const copy = document.createElement("button");
  const reveal = document.createElement("button");
  const note = document.createElement("div");
  note.className = "molab-init-note";
  const draw = () => {
    const shown = model.get("shown");
    code.textContent = shown ? (revealed && command ? command : shown) : "waiting for the browser's address...";
    copy.disabled = reveal.disabled = !command;
    reveal.textContent = revealed ? "hide token" : "show token";
    note.textContent = model.get("note");
  };
  const onMsg = (msg) => {
    if (msg && typeof msg.command === "string") {
      command = msg.command;
      draw();
    }
  };
  copy.textContent = "copy";
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(command);
      copy.textContent = "copied";
    } catch (e) {
      // no clipboard access: show and select the command for a manual copy
      revealed = true;
      draw();
      const range = document.createRange();
      range.selectNodeContents(code);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      copy.textContent = "copy the selection";
    }
    setTimeout(() => { copy.textContent = "copy"; }, 2000);
  };
  reveal.onclick = () => { revealed = !revealed; draw(); };
  model.on("msg:custom", onMsg);
  model.on("change:shown", draw);
  model.on("change:note", draw);
  box.append(code, copy, reveal);
  el.append(box, note);
  draw();
  model.send({ page: window.location.href });
  return () => {
    model.off("msg:custom", onMsg);
    model.off("change:shown", draw);
    model.off("change:note", draw);
  };
}
export default { render };
"""

_CSS = """
.molab-init { display: flex; gap: 0.5em; align-items: center; flex-wrap: wrap; }
.molab-init code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; padding: 0.35em 0.6em;
  border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 4px; overflow-wrap: anywhere; }
.molab-init button { font: inherit; padding: 0.25em 0.7em; cursor: pointer; }
.molab-init-note { margin-top: 0.4em; opacity: 0.75; font-size: 0.9em; }
"""

_widget_class = None


def _init_widget():
    global _widget_class
    if _widget_class is not None:
        return _widget_class
    try:
        import anywidget
        import traitlets
    except ImportError as e:
        raise ImportError("init_command() needs anywidget: pip install anywidget") from e

    class InitCommand(anywidget.AnyWidget):
        """`molab-slurm init <url> <token>` for the session this notebook runs in.

        Synced state (`url`, `shown`, `note`) holds nothing secret; the full command is the plain attribute
        `command`, and reaches the browser only as a message.
        """

        _esm = _ESM
        _css = _CSS
        url = traitlets.Unicode("").tag(sync=True)
        shown = traitlets.Unicode("").tag(sync=True)
        note = traitlets.Unicode("").tag(sync=True)

        def __init__(self, token: str | None, extra: list[str], **kwargs):
            self._molab_token, self._molab_extra = token, extra
            self.command = ""
            super().__init__(**kwargs)
            self.on_msg(self._on_browser_msg)
            self._update(None)

        def _on_browser_msg(self, _widget, content, _buffers):
            if isinstance(content, dict) and isinstance(content.get("page"), str):
                self._update(content["page"])
                self.send({"command": self.command})

        def _update(self, page: str | None):
            with self.hold_sync():
                self.url = connect_url(page or "") or ""
                self.command, self.shown = _command(self.url, self._molab_token, self._molab_extra)
                if self._molab_token is None:
                    self.note = (
                        "no token found on the marimo server's command line (not Linux, or started with "
                        "--no-token): fill in <token> from molab's connect snippet"
                    )
                elif not page:
                    self.note = "shown once this output is open in a browser"
                elif not self.url:
                    self.note = (
                        "this page's address is not an http(s) URL: use molab's connect snippet instead"
                    )
                else:
                    self.note = "the token is a shell on this box: treat it like an ssh key"

    _widget_class = InitCommand
    return InitCommand


def init_command(
    name: str | None = None, *, cpus: int | None = None, workdir: str | None = None, no_default: bool = False
):
    """A widget showing the `molab-slurm init` command that connects this session; display it in a notebook cell.

    `name`, `cpus`, `workdir` and `no_default` become `molab-slurm init`'s --name, --cpus, --workdir and --no-default.
    The command is complete once the notebook is open in a browser (the only place its public URL is known);
    the widget's `.url` and `.command` then hold it for Python too.
    """
    extra: list[str] = []
    for flag, value in (("--name", name), ("--cpus", cpus), ("--workdir", workdir)):
        if value is not None:
            extra += [flag, str(value)]
    if no_default:
        extra.append("--no-default")
    return _init_widget()(token=server_token(), extra=extra)
