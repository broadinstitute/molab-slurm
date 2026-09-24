---
title: Install and connect
nav_order: 2
---

# Install and connect

`molab-slurm` is one Python package with no dependencies. It needs Python ≥ 3.9
on your machine and the box's own `python3` on the other end — the CLI installs
nothing on the box. (The optional in-notebook helper `init_command()`, below,
is the exception: it runs in the notebook, so it needs the package there.)

```bash
uv tool install git+https://github.com/broadinstitute/molab-slurm
# or
pipx install git+https://github.com/broadinstitute/molab-slurm
```

Either one puts a single command, `molab-slurm`, on your `PATH`.

From a checkout, without installing:

```bash
git clone https://github.com/broadinstitute/molab-slurm && cd molab-slurm
PYTHONPATH=src python3 -m molab_slurm --help
```

## Connect a box

When you open a molab notebook and ask it to connect an agent, molab shows a
snippet with the notebook's URL and a token. Save both:

```bash
molab-slurm init https://sb-0123456789abcdef.sb.molab.run/ <token> --name gpu
```

Or let the session write that line for you. In a cell of its notebook:

```python
import molab_slurm as mos
mos.init_command(name="gpu")
```

shows `molab-slurm init <url> <token> --name gpu` for this session, the token hidden, with a copy button
(`--cpus`, `--workdir` and `--no-default` are `cpus=`, `workdir=` and `no_default=True`). The token comes from
the marimo server's command line, the URL from your browser -- the kernel only ever sees molab's internal proxy
address, so the line fills in once the notebook is open in a browser. This is the one part of molab-slurm that
runs in the notebook, so the package has to be in the notebook's environment: add
`git+https://github.com/broadinstitute/molab-slurm` in marimo's package manager (it is not on PyPI yet). It also
needs anywidget, which molab notebooks have; elsewhere `pip install anywidget`. See the [Python API](api.md).

`init` checks the token against the server **before** saving, then prints
what the box has:

```text
saved box 'gpu' to ~/.config/molab/config.json (default)
box       gpu  (https://sb-0123456789abcdef.sb.molab.run/)
host      a1b2c3d4-...-xyz12  python 3.13.11
cpus      4 (configured; the box reports the host's count)
gpus      1  (NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB)
disk      22.2 GB used under /marimo/.molab's filesystem
```

Options:

| option | default | meaning |
|---|---|---|
| `--name NAME` | `default` | what to call the box; `--box NAME` selects it later |
| `--cpus N` | 4 | the box's **real** CPU count. molab boxes report the host's (20+); jobs get `SLURM_CPUS_PER_TASK` capped at this |
| `--workdir DIR` | `/marimo` | where jobs run when neither `-D` nor `#SBATCH --chdir` says otherwise |
| `--no-default` | | save without making it the default |

A session's URL changes whenever molab recreates it, so run `init` again with
the new URL and the same `--name`. `molab-slurm boxes` lists what is saved; the
default is marked `*`.

## Choosing a box per command

In order of precedence:

1. `molab-slurm --box NAME <command>`
2. `MOLAB_URL` and `MOLAB_TOKEN` in the environment (no config file needed)
3. `MOLAB_BOX=NAME`
4. the default from `molab-slurm init`

## The token is a shell on the box

Anyone holding the token can run arbitrary code in the notebook's kernel, as
the user it runs as (root on molab). `molab-slurm init` writes it to
`~/.config/molab/config.json` with mode 600. Prefer that, or `MOLAB_TOKEN`, to
passing it on a command line where `ps` can see it, and never commit the
config file.
