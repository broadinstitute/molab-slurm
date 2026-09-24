---
title: Differences from SLURM
nav_order: 10
---

# Differences from SLURM

molab-slurm implements SLURM's *interface* on one box. Where the box cannot do
what a cluster does, it says so rather than pretending.

| | SLURM | molab-slurm |
|---|---|---|
| nodes | many | one: the session's box |
| resources | enforced (`--mem`, `--gres`, ...) | **not enforced**; accepted and listed as ignored. A job that exceeds the box's memory is killed by the kernel, not by a scheduler |
| array concurrency | as resources allow | `%1` unless you ask for `%N` — one box, usually one GPU |
| job ids | one per array task | one per job; array tasks are `A_a`, and `%j` expands to `A_a` |
| submit host | your login node | there is none: `SLURM_SUBMIT_DIR` is the working directory on the box |
| script | copied at submit time | read **in place** on the box when the task starts |
| `--export` | ships your environment | cannot: your laptop's environment never reaches the box. `ALL` means the box's; a bare `VAR=value` list keeps `ALL` (SLURM would imply `NONE`) |
| dependencies | `?` (OR), `singleton`, `aftercorr`, ... | `after`, `afterany`, `afterok`, `afternotok`; AND only |
| accounting | a database | files under `/marimo/.molab`, which live only as long as the session |
| time zone in `sacct` | the cluster's | your machine's |
| `OUT_OF_MEMORY` | the cgroup reports it | inferred: a task SIGKILLed by anything other than molab-slurm |

## Not implemented

`salloc`, `sattach`, `scontrol`, `sstat`, `sreport`, `--hold`/`release`,
requeueing, job steps inside a batch job, heterogeneous jobs, email.
