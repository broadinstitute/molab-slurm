# molab-slurm

[![CI](https://github.com/broadinstitute/molab-slurm/actions/workflows/ci.yml/badge.svg)](https://github.com/broadinstitute/molab-slurm/actions/workflows/ci.yml)
[![Docs](https://github.com/broadinstitute/molab-slurm/actions/workflows/pages.yml/badge.svg)](https://broadinstitute.github.io/molab-slurm/)

SLURM's commands — `sbatch`, `srun`, `squeue`, `sacct`, `scancel` — for a
molab box (marimo's cloud notebooks) you cannot ssh into.

molab-slurm is an independent project, not made or endorsed by marimo, which
makes molab. Its command is `molab-slurm`, not `molab`, so it cannot be
mistaken for an official molab tool or collide with one.

```console
$ molab-slurm init https://sb-0123456789abcdef.sb.molab.run/ $TOKEN --name gpu
$ molab-slurm sbatch -J bias_sweep --array=5-9%1 --wrap 'bash run_step.sh --array "$SLURM_ARRAY_TASK_ID" 03.0.train_bias_model.sh'
Submitted batch job 13
$ molab-slurm sbatch -J select_bias --dependency=afterok:13 --wrap 'bash run_step.sh 03.1.select_bias.sh'
Submitted batch job 14
$ molab-slurm squeue
JOBID       PARTITION  NAME         USER     ST  TIME  NODES  NODELIST(REASON)
13_5        molab      bias_sweep   me       R   4:12  1      gpu
13_[6-9%1]  molab      bias_sweep   me       PD  0:00  1      (JobArrayTaskLimit)
14          molab      select_bias  me       PD  0:00  1      (Dependency)
```

A molab session is a gVisor sandbox, usually with a GPU, reachable only
through its marimo notebook server. `molab-slurm` sends short Python snippets to the notebook
kernel over its HTTP API; a small runner on the box does the rest — array
tasks, `%N` throttling, `--time`, `--dependency`, cancellation — and writes
SLURM-shaped state that `squeue` and `sacct` read back. `#SBATCH` scripts
written for a cluster run as they are, provided the software they call exists
on the box.

* **No install on the box.** It needs only the box's own `python3`; the runner
  is copied over on every submit. Stdlib-only Python on both ends. (The optional
  `init_command()` notebook helper is the exception: it runs in the notebook.)
* **Jobs outlive your laptop.** Everything runs detached on the box; the CLI
  polls. Close the lid, come back, `molab-slurm tail -f 13_5`.
* **Nothing long runs in the kernel.** marimo interrupts the kernel when an
  agent's request times out or disconnects, which stops whatever the kernel is
  running, such as a training loop in a cell. molab-slurm's calls are short snippets
  and jobs run outside the kernel, so agents can poll while jobs keep running.
  See [how it works](docs/how-it-works.md#why-work-never-runs-in-the-kernel).
* **Honest about what it is not.** `--mem`, `--gres`, `--partition` and the
  rest are accepted and reported as ignored — there is no scheduler to
  enforce them.

**You start molab sessions yourself.** molab-slurm connects to a session
that is already running — `molab-slurm init` checks the URL and token and saves
them, nothing more. It never starts, restarts or sets up a session; when one
ends, open a new one in molab and `molab-slurm init` its new URL. Setting up the box
for your project is your project's own script, run with `molab-slurm srun`. See
[Sessions and recovery](docs/sessions.md).

**Documentation: <https://broadinstitute.github.io/molab-slurm/>**

Start with the [tutorials](https://broadinstitute.github.io/molab-slurm/tutorials/first-job.html);
`init_command()`, the config file and exit codes are in the
[Python API](https://broadinstitute.github.io/molab-slurm/api.html).

## Install

Installing gives you one command, `molab-slurm`:

```bash
uv tool install git+https://github.com/broadinstitute/molab-slurm   # or: pipx install ...
molab-slurm init <notebook-url> <token> --name gpu
molab-slurm sinfo
```

The token comes from the molab "connect" snippet — or, in a cell of the
session's notebook, `import molab_slurm as mos; mos.init_command(name="gpu")` shows the whole `molab-slurm init`
line with a copy button (the package has to be in the notebook's environment; see
[docs/install.md](docs/install.md#connect-a-box)). **The token is code execution on the box** — treat it like an ssh key. `molab-slurm init` stores it in
`~/.config/molab/config.json` with mode 600.

## Development

```bash
python -m pytest            # unit tests + the runner, run for real on this machine
PYTHONPATH=src python -m molab_slurm --help
```

See [docs/how-it-works.md](docs/how-it-works.md) for the design and
[docs/differences.md](docs/differences.md) for what differs from SLURM.

## License

MIT — see [LICENSE](LICENSE).
