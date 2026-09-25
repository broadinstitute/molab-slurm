# molab-slurm

SLURM's commands (`sbatch`, `srun`, `squeue`, `sacct`, `scancel`, `tail`, `put`, `get`, ...) for a molab box
reached only through its marimo notebook server. Every command lists sessions (`GET /api/sessions`) and makes
one or a few short requests to `POST /api/kernel/execute`, which run Python in the notebook kernel's
scratchpad; jobs run detached on the box under `runner.py`. Docs: `docs/` (published at
https://broadinstitute.github.io/molab-slurm/).

## Driving a box with molab-slurm

Full rules and the reasons: `docs/ai-agents.md`. In short:

- **Never poll the box.** No `watch molab-slurm squeue`, no background loops or monitors calling `squeue`,
  `sacct` or `tail`. Sessions have ended (`HTTP 410`, no reason shown, not restorable) while the box was
  being polled; a later session running jobs without polling has not ended so far. The cause is not
  confirmed: treat it as an observation, and still do not poll.
- `srun`, `sbatch --follow` and `tail -f` make two or more calls every 1-10 seconds while the job runs: use
  them only for commands that finish within a minute or so. Submit anything longer with `sbatch`.
- Submit, then block with `molab-slurm wait --after <expected run time> ID` (no calls until then, then one
  status call every 4 minutes; exits with the job's code), then `molab-slurm tail -n 30 ID` (never a full
  `tail` of a growing log) and `molab-slurm sacct -j ID` if it failed.
- One job per step, chained with `--dependency=afterok:ID` / `afterany:ID`; collect each step's results as
  soon as its job ends, and copy them off the box (a dead session takes everything on it).
- Upload many files as one archive (`put` once, one call per 512 KB; unpack with one short `srun`).
- At most one `keepalive` or `wait` at a time, no more often than their default (every 4 minutes).
- gVisor misreports resources: memory (`free` shows far more than the session has) and CPUs
  (`nproc` / `os.cpu_count()` give the host's count). Size thread pools and memory from the real numbers.
  Jobs get `SLURM_CPUS_PER_TASK`, and `OMP_NUM_THREADS` / `NUMBA_NUM_THREADS` unless already set, from `-c N`
  or the box's `init --cpus` (default 4); set other libraries' thread counts yourself, or use `taskset`.
- `HTTP 410 listing sessions`: the session is gone. Ask the user for a new connect snippet; sessions are
  started only by the user in molab.
- No long work in notebook cells or long `execute` requests: it blocks the kernel every call goes through.

## Working on this repository

- Standard library only, on the laptop and on the box (`dependencies = []`); `anywidget` is an optional extra
  for `molab_slurm.init_command()` inside a notebook. Everything, the runner included (it runs on the box's
  own `python3`), must keep working on Python 3.9 (`requires-python = ">=3.9"`).
- Tests (as CI runs them): `uv run --no-project --python 3.13 --with pytest --with anywidget python -m pytest -q`
  (also `--python 3.9`). The runner is exercised for real as a subprocess.
- Lint: `uvx ruff@0.16.8 check` (line length 110).
- Keep molab-slurm project-neutral: transport and scheduling only, no project-specific setup, examples or
  names in code or docs.
- Docs are a Jekyll site (just-the-docs theme) in `docs/`; each page has `title` and `nav_order` front matter
  (tutorials also `parent: Tutorials`), and pages link to each other with relative `.md` links.
