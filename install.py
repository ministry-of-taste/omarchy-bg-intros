#!/usr/bin/env python3
"""Install from this checkout; all destinations are per-user."""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "plugin"))
from lifecycle import install, uninstall

try:
    if sys.argv[1:] in ([], ["install"]):
        install(Path(__file__).resolve().parent / "plugin")
    elif sys.argv[1:] == ["uninstall"]:
        uninstall()
    else:
        raise RuntimeError("Usage: python3 install.py [install|uninstall]")
except (RuntimeError, OSError, ValueError) as error:
    print(f"bg-intros: {error}", file=sys.stderr)
    sys.exit(2)
