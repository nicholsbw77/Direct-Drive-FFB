"""
Resolve paths to bundled resources.

PyInstaller extracts files to a temp dir (_MEIPASS) when running as exe.
This helper finds the correct path whether running from source or as exe.
"""

import sys
from pathlib import Path


def resource_path(relative_path: str) -> Path:
    """
    Get the absolute path to a resource file.

    Works both when running from source (uses the repo root as base) and
    when running as a PyInstaller bundle (uses sys._MEIPASS as base).

    Args:
        relative_path: Path relative to the repo root, e.g. "config/foo.json"
                       or "assets/icon.ico".

    Returns:
        Absolute Path object.
    """
    if hasattr(sys, "_MEIPASS"):
        # Running as PyInstaller bundle — resources are in the temp extraction dir
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        # Running from source — repo root is two levels up from this file
        # (src/resource_path.py → src/ → repo root)
        base = Path(__file__).parent.parent
    return base / relative_path
