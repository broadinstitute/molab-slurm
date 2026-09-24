import pytest

from molab_slurm import notebook


@pytest.mark.parametrize(
    "page, url",
    [
        # molab: the browser shows the "-session" front end; the server is the same sandbox, at the root
        (
            "https://sb-0123456789abcdef-session.sb.molab.run/session/",
            "https://sb-0123456789abcdef.sb.molab.run/",
        ),
        (
            "https://sb-0123456789abcdef-session.sb.molab.run/session/?file=notebook.py#x",
            "https://sb-0123456789abcdef.sb.molab.run/",
        ),
        ("https://sb-0123456789abcdef.sb.molab.run/", "https://sb-0123456789abcdef.sb.molab.run/"),
        # a marimo server anywhere else: its own address, without the query
        ("http://localhost:2718/?file=nb.py", "http://localhost:2718/"),
        ("http://10.0.0.5:8080/base", "http://10.0.0.5:8080/base/"),
        ("http://10.0.0.5:8080/base/", "http://10.0.0.5:8080/base/"),
    ],
)
def test_connect_url(page, url):
    assert notebook.connect_url(page) == url


@pytest.mark.parametrize(
    "page",
    ["", "about:blank", "notebook.py", None, "vscode-webview://0a1b2c/index.html?id=x", "file:///tmp/x.html"],
)
def test_connect_url_not_a_url(page):
    assert notebook.connect_url(page) is None


@pytest.mark.parametrize(
    "argv, token",
    [
        (["python", "-m", "marimo", "edit", "nb.py", "--token", "--token-password", "abc123"], "abc123"),
        (["marimo", "edit", "--token-password=abc123", "nb.py"], "abc123"),
        (["marimo", "edit", "nb.py", "--no-token"], None),
        (["marimo", "edit", "nb.py", "--token-password"], None),  # dangling flag
        (["marimo", "edit", "--token-password-file", "/nonexistent/x"], None),
    ],
)
def test_token_from_argv(argv, token):
    assert notebook._token_from_argv(argv) == token


def test_token_from_password_file(tmp_path):
    f = tmp_path / "tok"
    f.write_text("s3cret\n")
    assert notebook._token_from_argv(["marimo", "edit", f"--token-password-file={f}"]) == "s3cret"
    # a relative path is the server's, resolved against its working directory, not the kernel's
    assert (
        notebook._token_from_argv(["marimo", "edit", "--token-password-file", "tok"], cwd=tmp_path)
        == "s3cret"
    )
    assert notebook._token_from_argv(["marimo", "edit", "--token-password-file", "-"], cwd=tmp_path) is None


def _fake_proc(root, pid, ppid, argv, comm="python", cwd=None):
    d = root / str(pid)
    d.mkdir()
    if cwd is not None:
        (d / "cwd").symlink_to(cwd)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1\n")


def test_server_token_walks_up_to_the_server(tmp_path):
    # molab: the server is PID 1; the kernel is a multiprocessing child of it
    _fake_proc(
        tmp_path, 1, 0, ["python", "-m", "marimo", "edit", "nb.py", "--token", "--token-password", "tok1"]
    )
    _fake_proc(
        tmp_path,
        99,
        1,
        ["/tmp/uv-venv/bin/python", "-c", "from multiprocessing.spawn import spawn_main"],
        comm="py (kernel) x",
    )  # a command name with spaces and parentheses
    assert notebook.server_token(pid=99, proc=tmp_path) == "tok1"


def test_server_token_reads_the_servers_password_file(tmp_path):
    home = tmp_path / "srv"
    home.mkdir()
    (home / "tok.txt").write_text("tok2\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    _fake_proc(proc, 1, 0, ["marimo", "edit", "nb.py", "--token-password-file", "tok.txt"], cwd=home)
    _fake_proc(proc, 50, 1, ["python", "kernel"], cwd=tmp_path)
    assert notebook.server_token(pid=50, proc=proc) == "tok2"


def test_server_token_none(tmp_path):
    _fake_proc(tmp_path, 1, 0, ["python", "-m", "marimo", "edit", "nb.py", "--no-token"])
    _fake_proc(tmp_path, 7, 1, ["python", "kernel"])
    assert notebook.server_token(pid=7, proc=tmp_path) is None
    assert notebook.server_token(pid=7, proc=tmp_path / "missing") is None


def test_command_quotes_and_hides_the_token():
    cmd, shown = notebook._command("https://sb-1.sb.molab.run/", "a'b", ["--name", "gpu"])
    assert cmd == "molab-slurm init https://sb-1.sb.molab.run/ 'a'\"'\"'b' --name gpu"
    assert shown == "molab-slurm init https://sb-1.sb.molab.run/ <token hidden> --name gpu"
    assert notebook._command("", "tok", []) == ("", "")
    assert notebook._command("https://x/", None, [])[0] == "molab-slurm init https://x/ <token>"


def _widget(monkeypatch, **kwargs):
    pytest.importorskip("anywidget")
    monkeypatch.setattr(notebook, "server_token", lambda: "S3CRET")
    w = notebook.init_command(**kwargs)
    sent = []
    monkeypatch.setattr(w, "send", lambda content, buffers=None: sent.append(content))
    return w, sent


PAGE = "https://sb-0123456789abcdef-session.sb.molab.run/session/eyJhdXRoX3Rva2VuIjoiUzNDUkVUIn0"


def test_widget_fills_in_once_the_browser_reports_its_address(monkeypatch):
    w, sent = _widget(monkeypatch, name="gpu", cpus=4, no_default=True)
    assert (w.url, w.command, w.shown) == ("", "", "")
    w._handle_custom_msg({"page": PAGE}, [])  # what the browser sends on render
    assert w.url == "https://sb-0123456789abcdef.sb.molab.run/"
    want = "molab-slurm init https://sb-0123456789abcdef.sb.molab.run/ S3CRET --name gpu --cpus 4 --no-default"
    assert w.command == want
    assert sent == [{"command": want}]
    assert w.shown == want.replace("S3CRET", "<token hidden>")


def test_widget_state_holds_no_secret(monkeypatch):
    # marimo saves synced widget state into its HTML/PDF exports: neither the token nor molab's page address
    # (which embeds it) may be part of it
    import json

    w, _ = _widget(monkeypatch, name="gpu")
    w._handle_custom_msg({"page": PAGE}, [])
    state = json.dumps(w.get_state(), default=str)
    assert "S3CRET" not in state and "eyJ" not in state
    assert w.url in state


def test_import_stays_stdlib_only():
    import subprocess
    import sys

    code = (
        "import sys, molab_slurm, molab_slurm.cli; "
        "assert not {'anywidget', 'traitlets', 'ipywidgets'} & set(sys.modules), sorted(sys.modules)"
    )
    src = str(__import__("pathlib").Path(notebook.__file__).parents[1])
    subprocess.run([sys.executable, "-c", code], check=True, env={"PYTHONPATH": src})


def test_init_command_without_anywidget(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "anywidget", None)
    monkeypatch.setattr(notebook, "_widget_class", None)
    with pytest.raises(ImportError, match="pip install anywidget"):
        notebook.init_command()


def test_widget_note_for_a_page_that_is_not_http(monkeypatch):
    w, sent = _widget(monkeypatch)
    w._handle_custom_msg({"page": "vscode-webview://0a1b2c/index.html?id=x"}, [])
    assert (w.url, w.command, w.shown) == ("", "", "")
    assert "not an http(s) URL" in w.note
    assert sent == [{"command": ""}]
