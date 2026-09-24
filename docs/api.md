---
title: Python API
nav_order: 6
---

# Python API
{: .no_toc }

molab-slurm's Python API is small: one function for a cell of the session's
own notebook, which writes the `molab-slurm init` line for that session, and the two
helpers it is built from. Everything else is the `molab-slurm` command
([Commands](commands.md)). The modules behind it (`cli`, `box`, `config`,
`remote`, `runner`, `slurm`) are not a public API; Python code that drives
molab-slurm runs the command, and [Configuration](#configuration) says what the
command reads.

1. TOC
{:toc}

## What is public

| name | what it is |
|---|---|
| `molab_slurm.init_command` | the widget below; the package's only export (`__all__`), also `molab_slurm.notebook.init_command` |
| `molab_slurm.__version__` | the version string, `"0.1.0"` |
| `molab_slurm.notebook.connect_url` | the notebook server's URL, from the address the browser shows |
| `molab_slurm.notebook.server_token` | the marimo server's token, from its command line |

`import molab_slurm` imports only the standard library, on your machine and in
a notebook. anywidget is imported when `init_command()` is first called.

## In the notebook

`init_command()` runs in the notebook's kernel, so the package must be
installed in the **notebook's** environment, not only on your machine: add
`git+https://github.com/broadinstitute/molab-slurm` in marimo's package
manager (it is not on PyPI yet; see [Install and connect](install.md)). It
also needs [anywidget](https://anywidget.dev/), which molab notebooks have;
elsewhere install it, or the package's `notebook` extra, which is
`anywidget>=0.9`.

## init_command

```python
molab_slurm.init_command(name: str | None = None, *, cpus: int | None = None, workdir: str | None = None,
                         no_default: bool = False)
```

The `molab-slurm init` command that connects this session, as a widget. Make it a
cell's output:

```python
import molab_slurm as mos
mos.init_command(name="gpu")
```

It shows `molab-slurm init https://sb-0123456789abcdef.sb.molab.run/ <token hidden> --name gpu`,
a **copy** and a **show token** button, and a note under them.

| parameter | becomes | notes |
|---|---|---|
| `name` | `--name NAME` | left out when `None`; `molab-slurm init` then saves the box as `default` |
| `cpus` | `--cpus N` | the box's real CPU count; `molab-slurm init`'s default is 4 |
| `workdir` | `--workdir DIR` | where jobs run by default; `molab-slurm init`'s default is `/marimo` |
| `no_default` | `--no-default` | added when `True`: save the box without making it the default |

Values go through `str()` and are shell-quoted: `workdir="/marimo/my project"`
gives `--workdir '/marimo/my project'`.

**Returns** an anywidget (an `anywidget.AnyWidget` subclass named
`InitCommand`). The token is read once, when `init_command()` is called, with
[`server_token()`](#server_token).

**Raises** `ImportError` when anywidget (or traitlets) cannot be imported:

```text
ImportError: init_command() needs anywidget: pip install anywidget
```

### When the command fills in

The kernel never sees the address you opened the notebook at, only molab's
internal proxy. The widget's front end sends `window.location.href` back to
Python when it renders, and [`connect_url()`](#connect_url) turns that into the
server's URL. So:

* until the widget has rendered in a browser, it shows `waiting for the
  browser's address...`, its buttons are disabled, and `url`, `command` and
  `shown` are `""`;
* a cell that reads `w.command` in the same run that created `w` gets `""`.
  Read it once the widget has shown, for example by running the reading
  cell again;
* in a page whose address is not `http(s)`, such as VS Code's notebook view,
  `connect_url()` returns `None` and the widget keeps waiting.

Once Python has the URL it sends the full command back to the browser as a
message, and the buttons come on. **copy** puts that command, token included,
on the clipboard. Without clipboard access it reveals the command and selects
it for a manual copy, and the button reads `copy the selection`. **show
token** / **hide token** switches what is drawn between `shown` and the full
command.

### Attributes

All are strings, set by Python.

| attribute | synced to the browser | value |
|---|---|---|
| `url` | yes, a trait | `connect_url()` of the browser's address, or `""` |
| `command` | no, a plain attribute | the full `molab-slurm init` line, token included; `""` until `url` is known |
| `shown` | yes, a trait | `command` with the token replaced by `<token hidden>`: what the widget draws |
| `note` | yes, a trait | the line under the command |

`url` and `command` are the ones meant for your code; `shown` and `note` are
for the display. The browser's address is not kept: it arrives as a message,
and only `url` is made from it.

```python
w = mos.init_command(name="gpu", cpus=4)
w
```

```python
# a later cell, once the widget has shown in the browser
w.url      # 'https://sb-0123456789abcdef.sb.molab.run/'
```

`note` is one of:

| note | when |
|---|---|
| `no token found on the marimo server's command line (not Linux, or started with --no-token): fill in <token> from molab's connect snippet` | `server_token()` returned `None`. Once the URL is known, `command` and `shown` hold the literal placeholder `<token>` |
| `shown once this output is open in a browser` | the browser has not reported its address yet |
| `this page's address is not an http(s) URL: use molab's connect snippet instead` | the browser reported an address `connect_url()` rejects, such as a VS Code notebook view |
| `the token is a shell on this box: treat it like an ssh key` | otherwise |

## connect_url

```python
molab_slurm.notebook.connect_url(page: str) -> str | None
```

The notebook server's URL, from the address the browser shows. It only parses
the string; it contacts nothing.

* molab shows a notebook at `https://sb-<id>-session.sb.molab.run/session/...`,
  a front end that rejects the token. A host of the form
  `sb-<id>-session.<domain>` becomes `<scheme>://sb-<id>.<domain>/`: `-session`
  dropped, the port kept, the path, query and fragment dropped.
* Any other address is taken to be the server's own: scheme, host, port and
  path kept, the path ending in one `/`, the query and fragment dropped.
* `None` unless `page` is an `http` or `https` URL with a host: `""`, `None`,
  `about:blank`, `notebook.py`, `file:///tmp/x.html`,
  `vscode-webview://0a1b2c/index.html?id=x`.

| `page` | returns |
|---|---|
| `https://sb-0123456789abcdef-session.sb.molab.run/session/?file=notebook.py` | `https://sb-0123456789abcdef.sb.molab.run/` |
| `https://sb-0123456789abcdef.sb.molab.run/` | `https://sb-0123456789abcdef.sb.molab.run/` |
| `http://localhost:2718/?file=nb.py` | `http://localhost:2718/` |
| `http://10.0.0.5:8080/base` | `http://10.0.0.5:8080/base/` |
| `about:blank` | `None` |

## server_token

```python
molab_slurm.notebook.server_token(pid: int | None = None, proc: str | os.PathLike = "/proc") -> str | None
```

The marimo server's auth token, read from its command line through `/proc`.
Linux only. It does not check the token; `molab-slurm init` does, before saving.

Starting at process `pid` (default: the calling process, `os.getpid()`), it
reads `<proc>/<pid>/cmdline` and looks for

* `--token-password VALUE` or `--token-password=VALUE`: returns `VALUE`;
* `--token-password-file PATH` or `--token-password-file=PATH`: returns the
  file's contents, stripped. A relative `PATH` is taken from that process's
  working directory (`<proc>/<pid>/cwd`); `-` (stdin) cannot be read back.

An empty value, `-`, or a file it cannot read counts as no token. If the process
has none, it moves to its parent (the parent PID from `<proc>/<pid>/stat`) and
looks again, at most 32 processes in all, the first included. The process
itself comes first because
`marimo run` runs the kernel in the server; under `marimo edit` the kernel is a
child of the server.

Returns `None` when

* `<proc>/<pid>/cmdline` cannot be read: there is no `/proc` (macOS), or no
  such process;
* no process up the chain has the flag: a server started with `--no-token`, or
  one whose token is not on its command line;
* the chain ends (a parent PID of 0, or the process's own), runs past 32
  processes, or a `stat` file cannot be read or parsed.

`proc` is a directory to read instead of `/proc`; the tests point it at a fake
process tree.

## The token

The token is code execution on the box: anyone holding it can run arbitrary
code in the notebook's kernel, as the user it runs as (root on molab).
`server_token()` reads only what code in that kernel can already see, so the
widget grants no new access. It does put the token where it can leak:

* `command` holds it in clear in the kernel, and once the widget has rendered
  the browser holds a copy too: **show token** only changes what is drawn;
* **copy** puts it on your clipboard;
* printing `w.command`, logging it, or a screenshot of the revealed widget
  shows it.

What it does not do is put the token in the widget's synced state, which
marimo saves into HTML and PDF exports: that state is `url`, `shown` and
`note`, and the full command reaches the browser only as a message. The
browser's address is not kept either, because on molab it embeds the token
as well.

Keep it where `molab-slurm init` puts it, `~/.config/molab/config.json` with mode
600, or in `MOLAB_TOKEN`, and never commit it.

## Configuration

What the `molab-slurm` command reads, for scripts that run it.

### The config file

`molab-slurm init` saves boxes to `~/.config/molab/config.json`
(`$XDG_CONFIG_HOME/molab/config.json` when that is set). It writes a temporary
file, sets it to mode 600 and renames it into place; the directory keeps its
usual permissions.

```json
{
  "default": "gpu",
  "boxes": {
    "gpu": {
      "cpus": 4,
      "workdir": "/marimo",
      "root": "/marimo/.molab",
      "url": "https://sb-0123456789abcdef.sb.molab.run/",
      "token": "<token>"
    }
  }
}
```

| field | meaning |
|---|---|
| `default` | the box used when nothing else picks one; set by every `molab-slurm init` without `--no-default`, and by the first one regardless |
| `boxes.NAME.url` | the notebook server's URL |
| `boxes.NAME.token` | its token |
| `boxes.NAME.cpus` | `--cpus`, default `4`: the box's real CPU count, the default and the cap for `SLURM_CPUS_PER_TASK`. molab boxes report the host's count (20+); the sessions molab-slurm was run on had a slice of about 4 |
| `boxes.NAME.workdir` | `--workdir`, default `/marimo`: where jobs run without `-D` or `#SBATCH --chdir` |
| `boxes.NAME.root` | `/marimo/.molab`: job state on the box. No `molab-slurm init` option sets it; a value edited into the file is used |

* `molab-slurm init` again with the same `--name` replaces `url` and `token` and
  keeps `cpus`, `workdir` and `root` unless you pass them again.
* `--no-default` leaves the default alone, except that the first box saved
  always becomes the default.
* A missing `cpus`, `workdir` or `root` is filled from the defaults when the
  file is read. A file that is not valid JSON stops every command that reads
  it with `molab-slurm: <path> is not valid JSON: ...`.
* `molab-slurm boxes` lists the saved boxes, without their tokens, the default
  marked `*`.

### Choosing a box

Every command except `init` and `boxes` picks one box, in this order:

1. `molab-slurm --box NAME <command>`: the saved box `NAME`, whatever the
   environment says. It must come before the command: after `sbatch` or
   `srun`, `--box` is accepted and not used, and the job goes to the box the
   rest of this list picks.
2. `MOLAB_URL`: that server, with `MOLAB_TOKEN` as its token. The config file
   is not read. The box is called `env` and gets the defaults (`cpus` 4,
   `workdir` `/marimo`, `root` `/marimo/.molab`), which no variable changes.
   Without `MOLAB_TOKEN` no token is sent.
3. `MOLAB_BOX=NAME`: the saved box `NAME`.
4. the config file's `default`.

When none of them names a box, or the name is not in the file (exit code 1):

```text
molab-slurm: no box configured: run `molab-slurm init <notebook-url> <token>` first
molab-slurm: no box named 'cpu' (known: gpu)
```

`--session ID`, the other option that goes before the command, picks the
notebook session when more than one notebook is open on the server (see
[Troubleshooting](troubleshooting.md)).

### Environment variables molab-slurm reads

| variable | effect |
|---|---|
| `MOLAB_URL` | use this server and ignore the config file, unless `--box` is given |
| `MOLAB_TOKEN` | the token for `MOLAB_URL`; read only when `MOLAB_URL` is set |
| `MOLAB_BOX` | a saved box to use instead of the default |
| `XDG_CONFIG_HOME` | where the config file lives (default `~/.config`) |
| `XDG_CACHE_HOME` | where `molab-slurm open` caches files (default `~/.cache`) |
| `LOGNAME`, `USER`, `LNAME`, `USERNAME` | the first one set is the name in `squeue`'s `USER` column (Python's `getpass.getuser()`, which falls back to the password database) |

Nothing else from your machine's environment reaches a job.

### What a job sees

The runner sets `SLURM_JOB_ID`, `SLURM_JOBID`, `SLURM_JOB_NAME`,
`SLURM_SUBMIT_DIR`, `SLURM_SUBMIT_HOST`, `SLURM_JOB_NODELIST`,
`SLURMD_NODENAME`, `SLURM_JOB_PARTITION`, `SLURM_NTASKS`, `SLURM_NNODES`,
`SLURM_CPUS_PER_TASK` and `SLURM_CPUS_ON_NODE`, and for array tasks
`SLURM_ARRAY_JOB_ID`, `SLURM_ARRAY_TASK_ID`, `SLURM_ARRAY_TASK_COUNT`,
`SLURM_ARRAY_TASK_MIN` and `SLURM_ARRAY_TASK_MAX`. Their values are in
[Batch scripts](batch-scripts.md#environment-variables-a-job-sees).

A job's environment is built in this order, later steps winning:

1. the box's environment, minus the notebook's Python: `PYTHONPATH`,
   `PYTHONHOME`, `PYTHONSAFEPATH`, `VIRTUAL_ENV` and `VIRTUAL_ENV_PROMPT`
   removed and the notebook venv's `bin` taken off `PATH`, unless
   `--notebook-env`. With `--export=NONE`, only `PATH`, `HOME`, `LANG`, `USER`
   and `TERM` of it;
2. `VAR=value` pairs from `--export`;
3. the `SLURM_*` variables, and `OMP_NUM_THREADS` and `NUMBA_NUM_THREADS` set
   to the CPU count unless steps 1 or 2 already set them;
4. whatever an `--rc` file exports: bash sources it in the job, before the
   script.

`--export` splits its value on commas, so a value with a comma in it cannot go
through `--export`:

```text
$ molab-slurm sbatch --export=SEEDS=1,2 --wrap 'bash run.sh'
molab-slurm: --export: '2' must be ALL, NONE or VAR=value
```

Put such variables in the command, `--wrap 'SEEDS=1,2 bash run.sh'`, or export
them from an `--rc` file.

### Exit codes

| exit code | when |
|---|---|
| `0` | success |
| `1` | an error reported as `molab-slurm: ...`: no box configured, the box unreachable or the token refused, `sbatch`'s script or working directory missing on the box, `no job N`, `init`'s `not saved: ...`; `scancel` when any id does not exist |
| `2` | a usage error: an unknown command; an unknown option or a missing argument to `init`, `boxes`, `squeue`, `sacct`, `scancel`, `sinfo`, `tail`, `put`, `get`, `open` or `keepalive`; an unrecognised short option to `sbatch` or `srun` (an unknown long option is accepted and listed as ignored); a malformed job id, a bad `#SBATCH` line, an invalid `--array`, `--time`, `--dependency`, `--cpus-per-task` or `--export`, `sbatch` with neither a script nor `--wrap`, `srun` with nothing to run, `scancel` with no ids and no `--all` |

`srun`, `sbatch --follow` and `tail -f` wait for a task and exit with its
result instead:

| exit code | when |
|---|---|
| the task's own | it exited non-zero; `127` when it could not be started |
| `128 + N` | it was ended by signal N: usually `143` (SIGTERM) after a cancel or `--time`, `137` (SIGKILL) for `OUT_OF_MEMORY` or a cancel that had to escalate |
| `0` | it ended `COMPLETED` |
| `1` | it ended with no exit code of its own: `NODE_FAIL`, or cancelled before it started |
| `130` | Ctrl-C: `srun` cancels the job first; `tail -f` and `--follow` stop watching and leave it running |
| `255` | the connection dropped; the job is still on the box, and `molab-slurm tail -f ID` picks it up again |

Once it is running, `keepalive` exits 0; a failed ping is printed, not fatal.

## Scripting molab-slurm from Python

Run the command. `--parsable` prints only the job id on stdout; notices such
as `molab-slurm: not enforced on molab, ignored: ...` go to stderr.

```python
import subprocess

def slurm_cmd(*args: str) -> str:
    """Run a molab-slurm command and return its stdout; raise CalledProcessError on a non-zero exit."""
    return subprocess.run(["molab-slurm", *args], check=True, capture_output=True, text=True).stdout

prev = slurm_cmd("sbatch", "--parsable", "-J", "setup", "-D", "/marimo/myproject", "setup.sh").strip()
for i, seeds in enumerate(["1,2", "3,4"]):
    prev = slurm_cmd("sbatch", "--parsable", f"--dependency=afterany:{prev}", "-J", f"train{i}",
                     "-D", "/marimo/myproject", "--wrap", f"SEEDS={seeds} bash train.sh").strip()
print(slurm_cmd("squeue"))
```

This submits the whole chain at once and returns; the jobs run one after the
other on the box. `afterany` starts the next job whatever state the previous
one ended in; with `afterok`, one failure cancels the rest of the chain
(`DependencyNeverSatisfied`). One heavy job at a time is also the safer shape:
a session running two GPU training jobs at once was killed after about 4.5
hours, most likely for memory. Have each job copy its results to a bucket as
it finishes and skip work whose outputs exist, and a resubmitted chain redoes
only what was lost (see [Sessions and recovery](sessions.md)).

To pick the box from Python, pass `--box NAME` first
(`slurm_cmd("--box", "gpu", "squeue")`), or set `MOLAB_BOX` (or `MOLAB_URL` and
`MOLAB_TOKEN`) in `env=`, as a copy of `os.environ` with the variable added:
`env=` replaces the whole environment, `PATH` included. Variables for the job
itself go in `--export=VAR=value`, or, when a value has a comma in it, in
`--wrap` or an `--rc` file, as above.
