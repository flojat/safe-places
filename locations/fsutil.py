"""Private Verzeichnisse unabhaengig von der Prozess-umask (z. B. Passenger) anlegen."""

from __future__ import annotations

import os
from pathlib import Path


def make_private_dirs(target: Path, root: Path) -> None:
    """Legt target (unterhalb von root) inkl. Zwischenordnern mit 0700 an."""
    target, root = Path(target), Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if root.stat().st_mode & 0o077:
        os.chmod(root, 0o700)
    current = root
    for part in target.relative_to(root).parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
            os.chmod(current, 0o700)  # mode von mkdir unterliegt der umask
        except FileExistsError:
            pass
