---
title: Tutorials
nav_order: 3
has_children: true
has_toc: false
---

# Tutorials

Three walk-throughs, each starting where the last one ends. They assume a molab
account that can start GPU sessions and a laptop with Python ≥ 3.9. Commands
that start with `molab` run on your laptop; everything they start runs on the box.

1. [Your first job](first-job.md) — install the CLI, connect a session from
   inside its own notebook, check the GPU, run a command with `srun` and a
   batch job with `sbatch`, watch it with `squeue`, `tail -f` and `sacct`, and
   copy a result back with `get`.
2. [A small pipeline](pipeline.md) — put a project on a fresh box, give jobs
   their environment with `--rc`, run a setup job, a training array throttled
   with `%N`, and a step that waits for it with `--dependency`; choose where
   logs go, cancel, and have each job copy its results to a bucket.
3. [When the session ends](new-session.md) — what is lost and what is kept,
   reconnecting the new session under the same name, checking its GPU, setting
   it up again, and resubmitting so finished work is skipped.

Every option is in [Commands](../commands.md); what molab-slurm cannot do is in
[Differences from SLURM](../differences.md).
