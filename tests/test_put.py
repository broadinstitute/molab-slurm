"""put: parts through the server's file API, appended by box.PUT_PART (run here for real), and the
kernel path for small files and for servers without the API."""

import contextlib
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from molab_slurm import box, cli
from molab_slurm.remote import MARK, Remote, UploadRetry, UploadUnavailable, snippet


def run_snippet(body, **params):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(snippet(body, **params), {})  # noqa: S102 -- what the kernel does with it
    text = out.getvalue()
    return json.loads(text[text.rindex(MARK) + len(MARK) :].splitlines()[0])


class FakeRemote:
    """The file API: writes each upload under a fresh name, as marimo does; `fail` lists what each
    upload attempt raises (None: it goes through)."""

    def __init__(self, *fail, damage=False):
        self.fail, self.damage, self.uploads = list(fail), damage, []

    def upload(self, directory, name, data):
        exc = self.fail.pop(0) if self.fail else None
        if exc:
            raise exc
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        while os.path.exists(path):
            path += "_1"
        with open(path, "wb") as fh:
            fh.write(data[:-1] + b"?" if self.damage else data)
        self.uploads.append(len(data))
        return path


class BoxCtx:
    def __init__(self, root, remote):
        self.box, self.remote, self.calls = {"root": str(root)}, remote, []

    def call(self, body, **params):
        self.calls.append(body)
        return run_snippet(body, **params)


@pytest.fixture
def small_parts(monkeypatch):
    monkeypatch.setattr(cli, "CHUNK", 1024)
    monkeypatch.setattr(cli, "PART", 4096)
    monkeypatch.setattr(cli, "PART_MIN", 1024)


def setup(tmp_path, size=10 << 10):
    src = tmp_path / "data.bin"
    src.write_bytes(os.urandom(size))
    src.chmod(0o750)
    out = tmp_path / "box" / "out"
    out.mkdir(parents=True)
    return src, out


def leftovers(tmp_path, out):
    return sorted(os.listdir(tmp_path / "root" / "uploads")) + [n for n in os.listdir(out) if n.startswith(".")]


def test_put_sends_parts_through_the_file_api(tmp_path, small_parts, capsys):
    src, out = setup(tmp_path)
    (out / "data.bin").write_bytes(b"old")
    ctx = BoxCtx(tmp_path / "root", FakeRemote())
    assert cli.cmd_put(ctx, [str(src), str(out)]) == 0
    assert (out / "data.bin").read_bytes() == src.read_bytes()  # replaced, not data_1.bin
    assert (out / "data.bin").stat().st_mode & 0o777 == 0o750
    assert ctx.remote.uploads == [4096, 4096, 2048]
    assert ctx.calls == [box.PUT_PART] * 3
    assert leftovers(tmp_path, out) == []
    assert capsys.readouterr().out == f"{out / 'data.bin'}\n"


def test_put_halves_a_part_that_is_refused_or_lost(tmp_path, small_parts):
    src, out = setup(tmp_path)
    ctx = BoxCtx(tmp_path / "root", FakeRemote(UploadRetry("HTTP 413"), None, UploadRetry("lost")))
    assert cli.cmd_put(ctx, [str(src), str(out / "new.bin")]) == 0
    assert (out / "new.bin").read_bytes() == src.read_bytes()
    assert ctx.remote.uploads == [2048] + [1024] * 8  # 413: 4096 -> 2048; lost: 2048 -> 1024


def test_put_gives_up_on_a_part_that_fails_twice_and_cleans_up(tmp_path, small_parts):
    src, out = setup(tmp_path)
    ctx = BoxCtx(tmp_path / "root", FakeRemote(None, UploadRetry("HTTP 413"), UploadRetry("HTTP 413")))
    with pytest.raises(SystemExit):
        cli.cmd_put(ctx, [str(src), str(out)])
    assert ctx.calls == [box.PUT_PART, box.PUT_ABORT]
    assert os.listdir(out) == [] and os.listdir(tmp_path / "root" / "uploads") == []


def test_put_interrupted_cleans_up(tmp_path, small_parts):
    src, out = setup(tmp_path)
    ctx = BoxCtx(tmp_path / "root", FakeRemote(None, KeyboardInterrupt()))
    with pytest.raises(SystemExit) as e:
        cli.cmd_put(ctx, [str(src), str(out)])
    assert e.value.code == 130 and ctx.calls == [box.PUT_PART, box.PUT_ABORT]
    assert os.listdir(out) == [] and os.listdir(tmp_path / "root" / "uploads") == []


def test_put_does_not_fall_back_once_parts_are_on_the_box(tmp_path, small_parts):
    src, out = setup(tmp_path)
    ctx = BoxCtx(tmp_path / "root", FakeRemote(None, UploadUnavailable("HTTP 404")))
    with pytest.raises(SystemExit):
        cli.cmd_put(ctx, [str(src), str(out)])
    assert ctx.calls == [box.PUT_PART, box.PUT_ABORT]  # no box.PUT: the file is not sent again
    assert os.listdir(out) == []


def test_put_refuses_a_damaged_part_and_leaves_the_destination(tmp_path, small_parts, capsys):
    src, out = setup(tmp_path)
    (out / "data.bin").write_bytes(b"old")
    ctx = BoxCtx(tmp_path / "root", FakeRemote(damage=True))
    with pytest.raises(SystemExit):
        cli.cmd_put(ctx, [str(src), str(out)])
    assert "damaged" in capsys.readouterr().err
    assert (out / "data.bin").read_bytes() == b"old"
    assert leftovers(tmp_path, out) == []


def test_put_small_file_is_one_kernel_call(tmp_path, small_parts):
    src, out = setup(tmp_path, size=1000)
    ctx = BoxCtx(tmp_path / "root", FakeRemote())
    assert cli.cmd_put(ctx, [str(src), str(out)]) == 0
    assert (out / "data.bin").read_bytes() == src.read_bytes()
    assert ctx.calls == [box.PUT] and ctx.remote.uploads == []


def test_put_goes_through_the_kernel_without_the_file_api(tmp_path, small_parts, capsys):
    src, out = setup(tmp_path)
    ctx = BoxCtx(tmp_path / "root", FakeRemote(UploadUnavailable("HTTP 404 from the server's file upload")))
    assert cli.cmd_put(ctx, [str(src), str(out)]) == 0
    assert (out / "data.bin").read_bytes() == src.read_bytes()
    assert ctx.calls == [box.PUT] * 10
    assert "through the kernel instead" in capsys.readouterr().err


# ── Remote.upload against a local server ─────────────────────────────────────


class Server:
    """Answers POST /api/files/create with `status` and `reply`, and keeps what it was sent."""

    def __init__(self, status=200, reply=None):
        self.status, self.reply, self.got = status, reply, {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.got = {"path": self.path, "headers": dict(self.headers), "body": body}
                data = json.dumps(outer.reply).encode()
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_upload_sends_one_multipart_body():
    data = os.urandom(3000) + b"\r\n--x\r\n"
    with Server(reply={"success": True, "info": {"path": "/stage/00_1"}}) as srv:
        assert Remote(srv.url, "tok", session="s").upload("/stage", "00", data) == "/stage/00_1"
    got, headers = srv.got, srv.got["headers"]
    boundary = headers["Content-Type"].split("boundary=")[1].encode()
    assert got["path"] == "/api/files/create" and headers["Authorization"] == "Bearer tok"
    assert headers["Content-Type"].startswith("multipart/form-data;")
    assert int(headers["Content-Length"]) == len(got["body"])
    for field in (b'name="path"\r\n\r\n/stage\r\n', b'name="type"\r\n\r\nfile\r\n', b'name="name"\r\n\r\n00\r\n'):
        assert field in got["body"]
    assert got["body"].startswith(b"--" + boundary)
    assert got["body"].endswith(b"application/octet-stream\r\n\r\n" + data + b"\r\n--" + boundary + b"--\r\n")


@pytest.mark.parametrize(
    "status, reply, exc",
    [(413, {}, UploadRetry), (524, {}, UploadRetry), (404, {}, UploadUnavailable),
     (200, {"success": False, "message": "nope"}, UploadUnavailable)],
)  # fmt: skip
def test_upload_errors(status, reply, exc):
    with Server(status, reply) as srv, pytest.raises(exc):
        Remote(srv.url, "tok", session="s").upload("/stage", "00", b"x")
