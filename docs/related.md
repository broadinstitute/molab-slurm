---
title: Related projects
nav_order: 12
---

# Related projects

Nothing we found does what molab-slurm does — SLURM's commands over a notebook
kernel's API, for a sandbox with no ssh — but several projects cover part of it.

| project | what it does | how it differs |
|---|---|---|
| [slurm_jupyter_kernel](https://github.com/pc2/slurm_jupyter_kernel) | starts Jupyter kernels as SLURM jobs | the opposite direction; needs a real SLURM cluster |
| [slurmpilot](https://pypi.org/project/slurmpilot/) | launches and tracks SLURM jobs on remote clusters from a laptop | ssh plus a real SLURM on the other end |
| [slurmjobs](https://pypi.org/project/slurmjobs/) | generates sbatch files; `ShellBatch` runs them locally | local only, no queue or state |
| [pysbatch](https://github.com/luptior/pysbatch), [slurmpy](https://github.com/brentp/slurmpy) | Python wrappers around `sbatch` | need real SLURM |
| SLURM magics for Jupyter (`%sbatch`, `%squeue`) | SLURM from inside a notebook | need real SLURM |
| SkyPilot | job queues on cloud VMs (`sky jobs launch/queue/cancel`) | provisions VMs and reaches them over ssh |
| pueue, task-spooler | local job queues with dependencies and parallelism limits | one machine, no remote API |
| submitit | Python job submission, with a local executor | a Python API rather than SLURM's CLI |

What molab-slurm adds is the transport (the notebook kernel, no daemon to
install), the gVisor-specific process handling, and enough of SLURM's CLI
that cluster scripts and habits carry over unchanged.
