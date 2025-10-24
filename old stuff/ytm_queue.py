#!/usr/bin/env python3
"""
ytm_queue.py — print YouTube URLs for a search query (no auth, no API).
Usage:
  ./ytm_queue.py "greek trap 2024" 30
"""
import subprocess, sys, shlex

query = sys.argv[1] if len(sys.argv) > 1 else "synthwave 2024"
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30

ytdlp = "./.venv/bin/yt-dlp"  # adjust if your venv lives elsewhere
match = "live_status!='is_live' & duration < 900"  # <15 min, not live

cmd = f'{shlex.quote(ytdlp)} --match-filter "{match}" --print url "ytsearch{limit}:{query}"'
res = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
print(res.stdout.strip())
