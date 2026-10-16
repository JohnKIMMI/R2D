"""Helpers for comparing against the legacy pipeline (TEST/analysis/coverage)."""
from pathlib import Path

LEGACY = Path(__file__).resolve().parent.parent.parent      # TEST/analysis/coverage
ROSA = LEGACY.parent.parent.parent                          # ~/Lab/ROSA


def resolve_binary(container_path):
    """The legacy metrics recorded the container path; map it back to the host."""
    if Path(container_path).is_file():          # already a host path
        return Path(container_path)
    # host builds live under /root/rosarum/targets (bind mount); the artifact image's own
    # binaries (/root/rosarum/authentic|synthetic) were copied to bins/ by name
    for pre, host in (("/root/rosarum/targets", ROSA / "rosarum" / "targets"),
                      ("/root/TEST", ROSA / "TEST")):
        if container_path.startswith(pre):
            return Path(str(host) + container_path[len(pre):])
    name = Path(container_path).name
    if (LEGACY / "bins" / name).is_file():
        return LEGACY / "bins" / name
    return Path(container_path)
