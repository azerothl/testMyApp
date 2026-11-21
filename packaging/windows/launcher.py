"""Point d’entrée de l’exécutable Windows autonome."""

import multiprocessing
import os
from pathlib import Path
import subprocess
import sys


def launch(arguments: list[str]) -> int:
    # L’exécutable est décompressé dans un nouveau dossier temporaire à chaque lancement.
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(local_app_data / "ms-playwright"))

    if arguments[:1] == ["install-browser"]:
        from playwright._impl._driver import compute_driver_executable, get_driver_env

        node, driver = compute_driver_executable()
        browsers = arguments[1:] or ["chromium"]
        return subprocess.call([node, driver, "install", *browsers], env=get_driver_env())

    from testmyapp.cli import main

    return main(arguments or ["gui"])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    raise SystemExit(launch(sys.argv[1:]))
