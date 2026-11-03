"""`dctl` command: runs the bundled Bash CLI (dctl.sh)."""
import os
import shutil
import sys

from dctl import __version__


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-V", "--version"):
        print("dctl", __version__)
        return
    if len(sys.argv) > 1 and sys.argv[1] in ("web", "gui", "ui"):
        from dctl.ui import main as ui_main
        sys.argv = [sys.argv[0]]
        return ui_main()
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dctl.sh")
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        sys.exit("dctl: the terminal CLI needs bash (Linux, macOS or WSL).\n"
                 "On Windows use the web UI instead:  dctl-ui")
    env = dict(os.environ, DCTL_PY=sys.executable)
    os.execve(bash, [bash, script] + sys.argv[1:], env)


if __name__ == "__main__":
    main()
