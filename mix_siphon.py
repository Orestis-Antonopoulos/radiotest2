#!/usr/bin/env python3
# mix_siphon.py
# Build a big-ass playlist from YouTube “Mix” seeds like “top 2025 songs”.
# Author: Shishou 😈

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from datetime import timedelta

DEF_SEEDS = [
    "top 2025 songs",
    "best songs 2025",
    "2025 hits",
    "top 2025 pop",
    "top 2025 rap",
    "charts 2025",
]

def run(cmd: str) -> subprocess.CompletedProcess:
    """Run a shell command and return CompletedProcess with UTF-8 text."""
    proc = subprocess.run(cmd, shell=True, capture_output=True)
    proc.stdout = proc.stdout.decode("utf-8", errors="replace")
    proc.stderr = proc.stderr.decode("utf-8", errors="replace")
    return proc

def get_seed_video_id(ytdlp: str, query: str) -> str | None:
    cmd = f'{shlex.quote(ytdlp)} --get-id {shlex.quote(f"ytsearch1:{query}")}'
    p = run(cmd)
    if p.returncode != 0 or not p.stdout.strip():
        print(f"[!] Failed to get seed for: {query}\n{p.stderr}", file=sys.stderr)
        return None
    return p.stdout.strip().splitlines()[0]

def expand_mix(ytdlp: str, seed_id: str, per_seed: int, match_filter: str) -> list[dict]:
    mix_url = f"https://www.youtube.com/watch?v={seed_id}&list=RD{seed_id}"
    cmd = (
        f'{shlex.quote(ytdlp)} --flat-playlist --playlist-end {per_seed} -j '
        f'--match-filter {shlex.quote(match_filter)} {shlex.quote(mix_url)}'
    )
    p = run(cmd)
    if p.returncode != 0:
        print(f"[!] yt-dlp error expanding mix for {seed_id}:\n{p.stderr}", file=sys.stderr)
        return []
    items = []
    for line in p.stdout.splitlines():
        try:
            obj = json.loads(line)
            # We only keep video entries (flat-playlist gives "url"/"id"/"title"/sometimes duration)
            if obj.get("_type") in (None, "url"):
                if obj.get("id"):
                    items.append(obj)
        except json.JSONDecodeError:
            continue
    return items

def human_time(seconds: int) -> str:
    return str(timedelta(seconds=seconds))

def main():
    ap = argparse.ArgumentParser(
        description="Siphon huge YouTube playlists from auto-generated Mixes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    ap.add_argument("--ytdlp", default="yt-dlp", help="Path to yt-dlp binary")
    ap.add_argument("--seed", action="append", dest="seeds", help="Seed queries (can repeat)")
    ap.add_argument("--seeds-file", help="File with one seed query per line")
    ap.add_argument("--per-seed", type=int, default=600, help="Max items to take per seed")
    ap.add_argument("--target-hours", type=float, default=24.0, help="Target total hours")
    ap.add_argument("--avg-seconds", type=int, default=210, help="Fallback avg song length in seconds")
    ap.add_argument("--output-prefix", default="mix", help="Output file prefix (produces .tsv, .m3u, urls.txt)")
    ap.add_argument("--play", action="store_true", help="Launch mpv on the result")
    ap.add_argument("--mpv", default="mpv", help="Path to mpv binary")
    ap.add_argument("--no-shuffle", action="store_true", help="Don’t shuffle when playing")
    ap.add_argument("--extra-filter", default="", help="Extra yt-dlp match-filter expression")
    args = ap.parse_args()

    seeds: list[str] = []
    if args.seeds:
        seeds.extend(args.seeds)
    if args.seeds_file:
        path = Path(args.seeds_file)
        if path.exists():
            seeds.extend([ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])
    if not seeds:
        seeds = DEF_SEEDS[:]

    # Core filters: kill live/was_live; allow user to add more
    match_filter = "!is_live & !was_live"
    if args.extra_filter.strip():
        match_filter = f"{match_filter} & ({args.extra_filter.strip()})"

    target_seconds = int(args.target_hours * 3600)
    avg_seconds = max(60, args.avg_seconds)

    print(f"[i] Seeds: {seeds}")
    print(f"[i] Target: {args.target_hours}h ≈ {target_seconds} sec")
    print(f"[i] Per-seed limit: {args.per_seed}")
    print(f"[i] Match-filter: {match_filter}")

    seen_ids: set[str] = set()
    rows: list[tuple[str, str, str, int]] = []  # (id, title, uploader, seconds)
    total = 0

    # Harvest
    for q in seeds:
        print(f"[*] Getting seed for: {q}")
        seed_id = get_seed_video_id(args.ytdlp, q)
        if not seed_id:
            continue
        print(f"    -> {seed_id} … expanding Mix")
        items = expand_mix(args.ytdlp, seed_id, args.per_seed, match_filter)
        print(f"    -> fetched {len(items)} entries from Mix")

        for it in items:
            vid = it.get("id")
            if not vid or vid in seen_ids:
                continue
            title = it.get("title") or ""
            up = it.get("uploader") or it.get("channel") or ""
            dur = it.get("duration")
            # flat playlist often lacks duration; fallback to average
            sec = int(dur) if isinstance(dur, (int, float)) else avg_seconds
            rows.append((vid, title, up, sec))
            seen_ids.add(vid)
            total += sec

            if total >= target_seconds:
                break
        if total >= target_seconds:
            break

    if not rows:
        print("[!] No results. Check yt-dlp installation or your queries.", file=sys.stderr)
        sys.exit(2)

    # Outputs
    prefix = Path(f"{args.output_prefix}")
    tsv_path = prefix.with_suffix(".tsv")
    urls_path = Path("urls.txt") if prefix.name == "mix" else Path(f"{prefix.name}_urls.txt")
    m3u_path = prefix.with_suffix(".m3u")

    # Write TSV
    with tsv_path.open("w", encoding="utf-8") as f:
        f.write("video_id\ttitle\tuploader\tduration_seconds\turl\n")
        for vid, title, up, sec in rows:
            url = f"https://www.youtube.com/watch?v={vid}"
            f.write(f"{vid}\t{title}\t{up}\t{sec}\t{url}\n")

    # Write URL list
    with urls_path.open("w", encoding="utf-8") as f:
        for vid, _, _, _ in rows:
            f.write(f"https://www.youtube.com/watch?v={vid}\n")

    # Write M3U
    with m3u_path.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for vid, title, up, sec in rows:
            f.write(f"#EXTINF:{sec},{up} - {title}\n")
            f.write(f"https://www.youtube.com/watch?v={vid}\n")

    print(f"[✓] Wrote: {tsv_path}")
    print(f"[✓] Wrote: {urls_path}")
    print(f"[✓] Wrote: {m3u_path}")
    print(f"[✓] Tracks: {len(rows)} | Total ~ {human_time(total)}")

    if args.play:
        shuffle = "" if args.no_shuffle else "--shuffle"
        cmd = (
            f'{shlex.quote(args.mpv)} --no-video '
            f'--ytdl-raw-options=match-filter="{match_filter}" '
            f'--loop-playlist=inf {shuffle} --playlist={shlex.quote(str(urls_path))}'
        )
        print(f"[*] Launching mpv:\n{cmd}")
        os.system(cmd)

if __name__ == "__main__":
    main()
