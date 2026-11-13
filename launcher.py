"""
Tiny launcher compiled to "IDX Monitor.exe".
Starts desktop_app.py with the project's Python environment (..\\.venv), without a console window.
Keeping the heavy code in the venv means the scheduled tasks and the app share one installation.
"""

import ctypes
import subprocess
import sys
from pathlib import Path

base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
pythonw = base.parent / ".venv" / "Scripts" / "pythonw.exe"
script = base / "desktop_app.py"

if not pythonw.exists() or not script.exists():
    ctypes.windll.user32.MessageBoxW(
        None,
        f"Python environment or app not found:\n{pythonw}\n{script}\n\nRun setup_new_pc.ps1 first.",
        "IDX Investing Monitor",
        0x10,
    )
    sys.exit(1)

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
subprocess.Popen([str(pythonw), str(script)], cwd=str(base), creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW, close_fds=True)
