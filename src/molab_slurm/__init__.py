"""molab-slurm: SLURM's commands (sbatch, squeue, scancel, ...) for a molab box."""

__version__ = "0.2.0"

from molab_slurm.notebook import init_command  # stdlib-only until called

__all__ = ["init_command"]
