"""Talking to a molab box: the notebook kernel's HTTP API is the only way in.

There is no ssh into a molab sandbox. The open notebook's server exposes
`POST /api/kernel/execute`, which runs Python in the kernel's scratchpad and
streams stdout/stderr back as server-sent events (the API the marimo-pair
skill uses). Every molab-slurm command is a short Python snippet sent this way;
the snippet prints one marked JSON line, which is the reply.

Keep calls SHORT. A request that runs for minutes has come back empty (the
cause is not known -- a proxy timeout is the likely one), which is why long
work is always a detached job on the box (see runner.py) that the CLI polls.

Anyone holding the token can run arbitrary code on the box -- it is
equivalent to a shell there. Treat it like an ssh key.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

MARK = "\x1emolab:"  # prefixes the one JSON line each snippet prints


class RemoteError(Exception):
    pass


class UploadUnavailable(RemoteError):
    """The server has no usable `POST /api/files/create` (an older marimo, or no edit rights)."""


class UploadRetry(RemoteError):
    """A part was refused as too large or lost on the way: send it again, smaller."""


def _tls_context() -> ssl.SSLContext:
    """Verified TLS, even on a Python that ships no CA bundle.

    python.org's macOS builds have an empty trust store until someone runs
    "Install Certificates.command", so urllib fails where curl works. Fall back
    to certifi, then to the OS bundle -- never to an unverified connection.
    """
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0):
        return ctx
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(bundle):
            return ssl.create_default_context(cafile=bundle)
    return ctx


TLS = _tls_context()


def snippet(body: str, **params) -> str:
    """Remote Python. `params` arrive as JSON -- never spliced into the code as
    text -- and the snippet answers with `_reply(obj)`."""
    return (
        "import json as _j\n"
        f"_p = _j.loads({json.dumps(json.dumps(params))})\n"
        "def _reply(obj):\n"
        f"    print({MARK!r} + _j.dumps(obj), flush=True)\n" + body
    )


class Remote:
    def __init__(self, url: str, token: str, session: str | None = None):
        self.base = url.rstrip("/")
        # Our own User-Agent: molab answered urllib's default
        # ("Python-urllib/3.x") with 403 before the token is even looked at.
        self.headers = {"User-Agent": "molab-slurm"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.session = session or self._only_session()

    def _only_session(self) -> str:
        req = urllib.request.Request(f"{self.base}/api/sessions", headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=30, context=TLS) as r:
                sessions = json.load(r)
        except urllib.error.HTTPError as e:
            raise RemoteError(f"{self.base}: HTTP {e.code} listing sessions (wrong token?)") from e
        except urllib.error.URLError as e:
            raise RemoteError(f"cannot reach {self.base}: {e.reason}") from e
        if not sessions:
            raise RemoteError("no active notebook session: open the notebook in a browser first")
        if len(sessions) > 1:
            names = ", ".join(f"{k} ({v.get('filename', '')})" for k, v in sessions.items())
            raise RemoteError(f"several sessions on this server, pick one with --session: {names}")
        return next(iter(sessions))

    def call(self, code: str, timeout: float = 120):
        """Run `code` in the kernel's scratchpad; return the JSON it replied with."""
        body = json.dumps({"code": code}).encode()
        headers = {
            **self.headers,
            "Content-Type": "application/json",
            "Marimo-Session-Id": self.session,
        }
        req = urllib.request.Request(
            f"{self.base}/api/kernel/execute", data=body, headers=headers, method="POST"
        )
        out, err, event = [], [], None
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=TLS) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        payload = json.loads(line[5:].strip())
                        if event == "stdout":
                            out.append(payload.get("data", ""))
                        elif event == "stderr":
                            err.append(payload.get("data", ""))
                        elif event == "done":
                            if payload.get("success") is False:
                                msg = (payload.get("error") or {}).get("msg", "remote error")
                                raise RemoteError(msg + "".join(err))
                            break
        except urllib.error.HTTPError as e:
            raise RemoteError(f"HTTP {e.code} from the kernel API") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise RemoteError(f"lost the connection to {self.base}: {e}") from e
        text = "".join(out)
        i = text.rfind(MARK)
        if i < 0:
            raise RemoteError("no reply from the kernel" + (": " + "".join(err) if err else ""))
        line = text[i + len(MARK) :].splitlines()[0]
        try:
            return json.loads(line)
        except ValueError as e:
            # The kernel API truncates a single reply at ~1 MB; a cut-off JSON
            # line is that, not a server bug. Callers chunk to stay under it.
            raise RemoteError(f"reply truncated after {len(line)} bytes (the kernel caps one reply at ~1 MB)") from e

    def upload(self, directory: str, name: str, data: bytes, timeout: float = 120) -> str:
        """Write `data` to a new file in `directory` on the box; return its path.

        Goes through the notebook server's own file API (`POST /api/files/create`,
        what its file browser uploads with), not the kernel: the bytes travel raw
        and the server streams them to disk. It never overwrites -- a name that
        exists becomes `name_1` -- so callers upload to a directory of their own
        and use the path it returns. molab's proxy refuses a body of 100 MB with
        413 (measured: 65 MB passes). `timeout` is per socket operation, not for
        the whole upload.
        """
        boundary = "molab-slurm-" + os.urandom(12).hex()
        head = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            for k, v in (("path", directory), ("type", "file"), ("name", name))
        )
        head += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        headers = {
            **self.headers,
            # set both: urllib would otherwise send form-urlencoded, and chunked encoding
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(head) + len(data) + len(tail)),
        }
        req = urllib.request.Request(
            f"{self.base}/api/files/create", data=[head, data, tail], headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=TLS) as r:
                reply = json.load(r)
        except urllib.error.HTTPError as e:
            # 413 and 502+ come from the proxy (too large, timed out); anything
            # else is marimo turning the request down.
            if e.code == 413 or e.code > 500:
                raise UploadRetry(f"HTTP {e.code} uploading {len(data) >> 20} MB") from e
            raise UploadUnavailable(f"HTTP {e.code} from the server's file upload") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise UploadRetry(f"lost the connection uploading {len(data) >> 20} MB: {e}") from e
        except ValueError as e:
            raise UploadUnavailable("the server's file upload did not answer in JSON") from e
        if not reply.get("success"):
            raise UploadUnavailable(f"the server's file upload refused it: {reply.get('message') or 'no reason'}")
        return reply["info"]["path"]
