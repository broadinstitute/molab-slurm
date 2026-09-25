---
title: For AI agents
nav_order: 13
---

# Instructions for AI agents

These rules are for coding agents (and scripts) that drive a molab box through
molab-slurm. People at a terminal should follow them too.

## Every call runs in the notebook kernel

A molab-slurm command is not a cheap status query. Each one lists the notebook
server's sessions (`GET /api/sessions`, skipped when you pass `--session`) and
then POSTs one or more Python snippets to `/api/kernel/execute`, which run in
the **notebook kernel's scratchpad** (see [How it works](how-it-works.md)).
`squeue`, `sacct`, `tail` and `put` are all kernel executions.

## Do not poll

Sessions have ended while molab-slurm was being polled. Several GPU sessions in
a row ended within minutes of their first jobs, with `HTTP 410` and no reason
shown in molab, while `watch -n 10 molab-slurm squeue` and an agent's loop
(checking `squeue` and `tail` every few minutes) were calling the box. The
deaths did not track memory or CPU use, and a notebook cell doing heavy GPU
work did not end its session. A later session, running jobs with nothing
polling, has not ended so far. The cause is not confirmed, but the cost of a
dead session is high: it cannot be restored, and the new one starts from
nothing.

So:

1. **No polling loops.** No `watch`, no background loops or monitors that call
   `squeue`, `sacct` or `tail` every few seconds or minutes.
2. **No streaming for long jobs.** `molab-slurm srun`, `sbatch --follow` and
   `tail -f` check the job's state and read its output (two or more calls)
   about every second, for as long as it runs. Use them only for commands that
   finish within a minute or so; submit anything longer with `sbatch`.
3. **Submit, then check once when the job should be done.** Estimate the run
   time and wait on your own machine (a local timer or `sleep` does not touch
   the box). Then make one check:

   ```bash
   molab-slurm sacct -j 12
   molab-slurm tail -n 30 12      # only the end of the output, not the whole file
   ```

   If the job is still running, estimate the rest and wait again. An early
   check to catch a quick failure (a minute or two after a job starts) is fine;
   a check every few minutes for hours is polling.
4. **One job per step.** Chain steps with `--dependency=afterok:<id>` (or
   `afterany`) instead of one long script, so a single `sacct` shows how far
   things got and each step's results can be collected as soon as its job
   ends.
5. **Few calls to start.** Every command is at least one call, and `put` makes
   one per 512 KB. Pack many small files into one archive, `put` it once and
   unpack it with one short `srun`; move anything large through a bucket.
6. **Copy results off the box as each job finishes.** A session can end at any
   time and cannot be brought back; whatever is only on the box is lost with
   it.
7. **At most one `keepalive`, at its default interval or longer** (every 4
   minutes). It is a call on a timer too, so run it only if you need it, and
   not one per agent or script.

## What gVisor misreports

The box runs in a gVisor sandbox that reports the host's resources, not the
session's:

* **Memory:** `free` shows far more than the session has; on one session the
  real amount was about 32 GB. Size memory use from the real number, and watch
  your jobs' own RSS.
* **CPUs:** `nproc` and `os.cpu_count()` return the host's count (20 reported
  on a 4-CPU box). Every job gets `SLURM_CPUS_PER_TASK` set to the box's
  configured CPU count (`molab-slurm init --cpus`, 4 by default) or to `-c N`,
  and `OMP_NUM_THREADS` and `NUMBA_NUM_THREADS` set to the same unless they are
  already set. Other libraries that size their thread pools from
  `os.cpu_count()` oversubscribe the real CPUs: set their thread counts
  explicitly or pin the job with `taskset -c 0-<N-1>`. Leaving one CPU free
  (`-c 3` on a 4-CPU box) keeps the notebook kernel, and so molab-slurm itself,
  responsive.

## When the session is gone

`HTTP 410 listing sessions` means the session has ended. Do not retry in a loop
and do not try to recreate it: sessions are started by the user in molab
([Sessions and recovery](sessions.md)). Tell the user, ask for the new connect
snippet, run `molab-slurm init` with it, check `molab-slurm sinfo` (especially
that the GPU is there), rerun the project's setup and resubmit only the steps
whose results were not copied off.

## Heavy work stays out of the notebook

Long work in a notebook cell, or in one long `execute` request, blocks the
kernel that every molab-slurm call goes through, and a request that gives up
can interrupt it. Run it with `molab-slurm sbatch`.
