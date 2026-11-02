"""Small OS-specific helpers (macOS and Windows)."""
import os
import subprocess
import sys

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
OS_NAME = "windows" if IS_WIN else "mac"


def open_path(target):
    """Open a file, folder or URL with the default application."""
    if IS_WIN:
        os.startfile(str(target))  # noqa: S606
    else:
        subprocess.run(["open", str(target)])


def reveal(path):
    """Show a file selected in Finder / Explorer."""
    if IS_WIN:
        subprocess.run(["explorer", f"/select,{os.path.normpath(str(path))}"])
    else:
        subprocess.run(["open", "-R", str(path)])


def run_in_terminal(command):
    """Run a command in a visible terminal window (sign-ins need the user)."""
    if IS_WIN:
        subprocess.Popen(f"start cmd /k {command}", shell=True)
    else:
        script = f'tell application "Terminal" to do script "{command}"\ntell application "Terminal" to activate'
        subprocess.run(["osascript", "-e", script])
