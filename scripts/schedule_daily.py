"""Daily TrendTide cycle on Windows Task Scheduler — transparent: prints the exact command, creates it only with --create.

    py scripts\\schedule_daily.py                 # show the command
    py scripts\\schedule_daily.py --create        # register "TrendTide-Daily" for the current user
    py scripts\\schedule_daily.py --remove        # delete it
Runs `trendtide run` (safe cycle: discover -> briefs -> render approved -> manifest). Add --upload for PRIVATE uploads.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = "TrendTide-Daily"

ap = argparse.ArgumentParser()
ap.add_argument("--at", default="09:17")
ap.add_argument("--upload", action="store_true")
ap.add_argument("--create", action="store_true")
ap.add_argument("--remove", action="store_true")
a = ap.parse_args()
exe = ROOT / ".venv" / "Scripts" / "trendtide.exe"
action = f'cmd /c cd /d "{ROOT}" && "{exe}" run' + (" --execute" if a.upload else "")
create = ["schtasks", "/Create", "/TN", TASK, "/SC", "DAILY", "/ST", a.at, "/TR", action, "/F"]
if a.remove:
    subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], check=False)
elif a.create:
    subprocess.run(create, check=True)
    print(f"Created {TASK} at {a.at}. Remove with: py scripts\\schedule_daily.py --remove")
else:
    print("Command that --create would run:\n  " + subprocess.list2cmdline(create))
