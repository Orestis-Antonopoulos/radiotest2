#!/usr/bin/env python3
# mix_siphon.py
# Build a big-ass playlist from YouTube “Mix” seeds like “top 2025 songs”.
# Filters out long videos by probing duration when the flat playlist lacks it.
# Author: Shishou 😈 (anti-2h-mix edition)

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from datetime import timedelta
from typing import Optional, Dict, Any, List

DEF_SEEDS = [
    "top 2020 pop",
    "2020s pop",
    "2000 pop songs",
    "top 2010 pop",
]

def run(cmd: str) -> subprocess.CompletedProcess:
    """Run a shell command and return CompletedProcess with UTF-8 text."""
    proc = subprocess.run(cmd, shell=True, capture_output=True)
    proc.stdout = proc.stdout.decode("utf-8", errors="replace")
    proc.stderr = proc.stderr.decode("utf-8", errors="replace")
    return proc

def get_seed_video_id(ytdlp: str, query: str, ytdlp_args: str) -> Optional[str]:
    cmd = f'{shlex.quote(ytdlp)} {ytdlp_args} --get-id {shlex.quote(f"ytsearch1:{query}")}'.strip()
    p = run(cmd)
    if p.returncode != 0 or not p.stdout.strip():
        print(f"[!] Failed to get seed for: {query}\n{p.stderr}", file=sys.stderr)
        return None
    return p.stdout.strip().splitlines()[0]

def expand_mix(ytdlp: str, seed_id: str, per_seed: int, match_filter: str, ytdlp_args: str) -> List[Dict[str, Any]]:
    mix_url = f"https://www.youtube.com/watch?v={seed_id}&list=RD{seed_id}"
    cmd = (
        f'{shlex.quote(ytdlp)} {ytdlp_args} --flat-playlist --playlist-end {per_seed} -j '
        f'--match-filter {shlex.quote(match_filter)} {shlex.quote(mix_url)}'
    ).strip()
    p = run(cmd)
    if p.returncode != 0:
        print(f"[!] yt-dlp error expanding mix for {seed_id}:\n{p.stderr}", file=sys.stderr)
        return []
    items = []
    for line in p.stdout.splitlines():
        try:
            obj = json.loads(line)
            # keep video-like things
            if obj.get("_type") in (None, "url", "video"):
                if obj.get("id"):
                    items.append(obj)
        except json.JSONDecodeError:
            continue
    return items

def probe_info(ytdlp: str, video_id: str, probe_args: str) -> Optional[Dict[str, Any]]:
    """
    Fetch full JSON for a single video to get reliable duration/title/uploader.
    Uses iOS client by default (can be overridden via probe_args).
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = f'{shlex.quote(ytdlp)} -j --no-playlist {probe_args} {shlex.quote(url)}'
    p = run(cmd)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return None

def human_time(seconds: int) -> str:
    return str(timedelta(seconds=seconds))

def main():
    ap = argparse.ArgumentParser(
        description="Siphon huge YouTube playlists from auto-generated Mixes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    ap.add_argument("--ytdlp", default="yt-dlp", help="Path to yt-dlp binary")

    # Extra args passthroughs
    ap.add_argument("--ytdlp-arg", action="append", default=[],
                    help="Extra arg for yt-dlp (repeatable), e.g. --ytdlp-arg=--cookies-from-browser --ytdlp-arg=chrome")
    ap.add_argument("--ytdlp-args", default="", help="Extra args for yt-dlp as a single string")

    ap.add_argument("--seed", action="append", dest="seeds", help="Seed queries (can repeat)")
    ap.add_argument("--seeds-file", help="File with one seed query per line")
    ap.add_argument("--per-seed", type=int, default=600, help="Max items to take per seed")

    ap.add_argument("--target-hours", type=float, default=24.0, help="Target total hours")
    ap.add_argument("--avg-seconds", type=int, default=210, help="Fallback avg song length in seconds")

    # Hard cap video length (the fix you want)
    ap.add_argument("--max-seconds", type=int, default=1200, help="Drop videos longer than this (e.g., 1200 = 20 min)")
    ap.add_argument("--probe-limit", type=int, default=200, help="Max number of duration probes (to avoid hammering)")

    ap.add_argument("--output-prefix", default="mix", help="Output file prefix (produces .tsv, .m3u, urls.txt)")
    ap.add_argument("--play", action="store_true", help="Launch mpv on the result")
    ap.add_argument("--mpv", default="mpv", help="Path to mpv binary")
    ap.add_argument("--no-shuffle", action="store_true", help="Don’t shuffle when playing")
    ap.add_argument("--extra-filter", default="", help="Extra yt-dlp match-filter expression")
    args = ap.parse_args()

    # Build ytdlp args strings (for shell safety)
    ytdlp_extra = " ".join(shlex.quote(a) for a in (args.ytdlp_arg or []))
    if args.ytdlp_args:
        ytdlp_extra = f"{ytdlp_extra} {args.ytdlp_args}".strip()

    # Good: plain web client, no tokens needed
    default_probe = "--extractor-args youtube:player_client=web --force-ipv4 --concurrent-fragments 1 --http-chunk-size 10M"
    probe_args = f"{ytdlp_extra} {default_probe}".strip()


    seeds: List[str] = []
    if args.seeds:
        seeds.extend(args.seeds)
    if args.seeds_file:
        path = Path(args.seeds_file)
        if path.exists():
            seeds.extend([ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])
    if not seeds:
        seeds = DEF_SEEDS[:]

    # Core filters: kill live/was_live; allow user to add more (this only helps if fields exist!)
    # We ALSO enforce --max-seconds via probe when duration is missing.
    match_filter = "!is_live & !was_live"
    if args.extra_filter.strip():
        match_filter = f"{match_filter} & ({args.extra_filter.strip()})"
    # try to filter by duration as well (works when duration is present)
    match_filter = f"{match_filter} & duration <= {max(1, args.max_seconds)}"

    target_seconds = int(args.target_hours * 3600)
    avg_seconds = max(60, args.avg_seconds)
    max_seconds = max(60, args.max_seconds)
    probe_limit = max(0, args.probe_limit)

    print(f"[i] Seeds: {seeds}")
    print(f"[i] Target: {args.target_hours}h ≈ {target_seconds} sec")
    print(f"[i] Per-seed limit: {args.per_seed}")
    print(f"[i] Match-filter: {match_filter}")
    print(f"[i] Max seconds per track: {max_seconds} (probes up to {probe_limit})")

    seen_ids: set[str] = set()
    rows: List[tuple[str, str, str, int]] = []  # (id, title, uploader, seconds)
    total = 0
    probes_used = 0

    # Harvest
    for q in seeds:
        print(f"[*] Getting seed for: {q}")
        seed_id = get_seed_video_id(args.ytdlp, q, ytdlp_extra)
        if not seed_id:
            continue
        print(f"    -> {seed_id} … expanding Mix")
        items = expand_mix(args.ytdlp, seed_id, args.per_seed, match_filter, ytdlp_extra)
        print(f"    -> fetched {len(items)} entries from Mix")

        for it in items:
            vid = it.get("id")
            if not vid or vid in seen_ids:
                continue

            title = (it.get("title") or "").strip()
            up = (it.get("uploader") or it.get("channel") or "").strip()

            dur = it.get("duration")
            # If duration is missing or suspicious, probe once
            if not isinstance(dur, (int, float)) or int(dur) > max_seconds:
                if probes_used >= probe_limit:
                    # If we can't probe more, skip unknown/too-long entries
                    continue
                meta = probe_info(args.ytdlp, vid, probe_args)
                probes_used += 1
                if not meta or not isinstance(meta.get("duration"), (int, float)):
                    # still no duration? skip to be safe
                    continue
                dur = int(meta["duration"])
                # fill better metadata if we got it
                title = (meta.get("title") or title).strip()
                up = (meta.get("uploader") or meta.get("channel") or up).strip()

            sec = int(dur)
            if sec > max_seconds:
                # too long, skip
                continue

            rows.append((vid, title, up, sec))
            seen_ids.add(vid)
            total += sec

            if total >= target_seconds:
                break
        if total >= target_seconds:
            break

    if not rows:
        print("[!] No results (everything too long or probing failed). "
              "Try raising --probe-limit or --max-seconds, or add cookies via "
              "--ytdlp-arg=--cookies-from-browser --ytdlp-arg=chrome", file=sys.stderr)
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
    print(f"[✓] Tracks: {len(rows)} | Total ~ {human_time(total)} | Probes used: {probes_used}")

    if args.play:
        shuffle = "" if args.no_shuffle else "--shuffle"
        # NOTE: mpv streaming from YT is optional; you’re downloading elsewhere anyway.
        cmd = (
            f'{shlex.quote(args.mpv)} --no-video '
            f'--ytdl-raw-options=match-filter="{match_filter}" '
            f'--loop-playlist=inf {shuffle} --playlist={shlex.quote(str(urls_path))}'
        )
        print(f"[*] Launching mpv:\n{cmd}")
        os.system(cmd)

if __name__ == "__main__":
    main()
