#!/usr/bin/env python3
# mix_siphon.py (v2) — Build a long playlist from YouTube "Mix" seeds.
# - Searches seed queries (e.g., "top 2025 songs")
# - Forces YouTube Mix per seed
# - Expands up to N items per seed (flat, fast)
# - Filters out live/was_live
# - Dedupe + stop at target duration
# - Writes: mix.tsv, urls.txt (or <prefix>_urls.txt), mix.m3u
# - Optional: _with_jingles.m3u interleaving a local jingle every N tracks
# - Optional: quick play with mpv

import argparse
import json
import os
import random
import shlex
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

DEF_SEEDS = [
    "top 2025 songs",
    "best songs 2025",
    "2025 hits",
    "top 2025 pop",
    "top 2025 rap",
    "charts 2025",
]

def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

def human_time(seconds):
    return str(timedelta(seconds=int(seconds)))

def get_seed_video_id(ytdlp, query, extra_args):
    cmd = f'{shlex.quote(ytdlp)} --get-id {shlex.quote("ytsearch1:"+query)}'
    if extra_args:
        cmd += " " + extra_args
    p = run(cmd)
    if p.returncode != 0 or not p.stdout.strip():
        print(f"[!] Failed to get seed for: {query}\n{p.stderr}", file=sys.stderr)
        return None
    return p.stdout.strip().splitlines()[0]

def expand_mix(ytdlp, seed_id, per_seed, match_filter, extra_args):
    mix_url = f"https://www.youtube.com/watch?v={seed_id}&list=RD{seed_id}"
    # Flat playlist as NDJSON lines
    cmd = (
        f'{shlex.quote(ytdlp)} --flat-playlist --playlist-end {int(per_seed)} -j '
        f'--match-filter {shlex.quote(match_filter)} {shlex.quote(mix_url)}'
    )
    if extra_args:
        cmd += " " + extra_args
    p = run(cmd)
    if p.returncode != 0:
        print(f"[!] yt-dlp error expanding mix for {seed_id}:\n{p.stderr}", file=sys.stderr)
        return []
    items = []
    for ln in p.stdout.splitlines():
        try:
            obj = json.loads(ln)
            # flat entries typically _type=url, keep those with id
            if obj.get("id"):
                items.append(obj)
        except Exception:
            continue
    return items

def write_outputs(prefix_path, rows):
    prefix = Path(prefix_path)
    tsv_path = prefix.with_suffix(".tsv")
    urls_path = Path("urls.txt") if prefix.name == "mix" else Path(prefix.stem + "_urls.txt")
    m3u_path = prefix.with_suffix(".m3u")

    with tsv_path.open("w", encoding="utf-8") as f:
        f.write("video_id\ttitle\tuploader\tduration_seconds\turl\n")
        for vid, title, up, sec in rows:
            f.write(f"{vid}\t{title}\t{up}\t{sec}\thttps://www.youtube.com/watch?v={vid}\n")

    with urls_path.open("w", encoding="utf-8") as f:
        for vid, _, _, _ in rows:
            f.write(f"https://www.youtube.com/watch?v={vid}\n")

    with m3u_path.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for vid, title, up, sec in rows:
            f.write(f"#EXTINF:{sec},{up} - {title}\n")
            f.write(f"https://www.youtube.com/watch?v={vid}\n")

    print(f"[✓] Wrote: {tsv_path}")
    print(f"[✓] Wrote: {urls_path}")
    print(f"[✓] Wrote: {m3u_path}")
    return urls_path, m3u_path

def write_with_jingles(m3u_in, jingles_dir, period, out_suffix="_with_jingles"):
    jroot = Path(jingles_dir)
    if not jroot.exists():
        print(f"[!] Jingles dir not found: {jroot} — skipping jingles.", file=sys.stderr)
        return None
    jingles = [p for p in jroot.rglob("*") if p.suffix.lower() in (".mp3",".m4a",".wav",".flac")]
    if not jingles:
        print(f"[!] No audio files in {jroot} — skipping jingles.", file=sys.stderr)
        return None
    m3u_in = Path(m3u_in)
    lines = m3u_in.read_text(encoding="utf-8").splitlines()
    header = []
    tracks = []
    for ln in lines:
        if ln.startswith("#"):
            header.append(ln)
        else:
            if ln.strip():
                tracks.append(ln.strip())
    out_lines = ["#EXTM3U"]
    count = 0
    for t in tracks:
        out_lines.append(t)
        count += 1
        if period > 0 and count % period == 0:
            out_lines.append(str(random.choice(jingles)))
    out_path = m3u_in.with_name(m3u_in.stem + out_suffix + m3u_in.suffix)
    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"[✓] Wrote: {out_path} (jingle every {period} tracks)")
    return out_path

def main():
    ap = argparse.ArgumentParser(
        description="Siphon huge YouTube playlists from auto-generated Mixes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    ap.add_argument("--ytdlp", default="yt-dlp", help="Path to yt-dlp")
    ap.add_argument("--ytdlp-args", default="", help="Extra args for yt-dlp (quoted). E.g. '--cookies cookies.txt --extractor-args youtube:player_client=android'")
    ap.add_argument("--seed", action="append", dest="seeds", help="Seed query (repeatable)")
    ap.add_argument("--seeds-file", help="File with one seed query per line")
    ap.add_argument("--per-seed", type=int, default=600, help="Max items per seed Mix")
    ap.add_argument("--target-hours", type=float, default=24.0, help="Target total hours")
    ap.add_argument("--avg-seconds", type=int, default=210, help="Fallback avg track length (sec) when duration unknown")
    ap.add_argument("--output-prefix", default="mix", help="Output prefix (writes .tsv, .m3u, and urls.txt or <prefix>_urls.txt)")
    ap.add_argument("--extra-filter", default="", help="Extra yt-dlp match-filter expression (ANDed).")
    ap.add_argument("--play", action="store_true", help="Launch mpv on the result")
    ap.add_argument("--mpv", default="mpv", help="Path to mpv")
    ap.add_argument("--no-shuffle", action="store_true", help="Don’t shuffle when playing")
    ap.add_argument("--jingles-dir", default="", help="If set, also create <prefix>_with_jingles.m3u by inserting a random jingle every N tracks")
    ap.add_argument("--jingle-period", type=int, default=0, help="Insert one random jingle every N tracks (requires --jingles-dir)")
    args = ap.parse_args()

    # Seeds
    seeds = []
    if args.seeds:
        seeds.extend(args.seeds)
    if args.seeds_file:
        p = Path(args.seeds_file)
        if p.exists():
            seeds.extend([ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()])
    if not seeds:
        seeds = DEF_SEEDS[:]

    # Filters
    match_filter = "!is_live & !was_live"
    if args.extra_filter.strip():
        match_filter = f"{match_filter} & ({args.extra_filter.strip()})"

    target_seconds = int(args.target_hours * 3600)
    avg_seconds = max(60, int(args.avg_seconds))

    print(f"[i] Seeds: {seeds}")
    print(f"[i] Target: {args.target_hours}h ≈ {target_seconds} sec")
    print(f"[i] Per-seed limit: {args.per_seed}")
    print(f"[i] Match-filter: {match_filter}")

    seen = set()
    rows = []  # (id, title, uploader, seconds)
    total = 0

    for q in seeds:
        print(f"[*] Seed: {q}")
        seed_id = get_seed_video_id(args.ytdlp, q, args.ytdlp_args)
        if not seed_id:
            continue
        print(f"    -> {seed_id} … expanding Mix")
        items = expand_mix(args.ytdlp, seed_id, args.per_seed, match_filter, args.ytdlp_args)
        print(f"    -> fetched {len(items)} entries")

        for it in items:
            vid = it.get("id")
            if not vid or vid in seen:
                continue
            title = it.get("title") or ""
            up = it.get("uploader") or it.get("channel") or ""
            dur = it.get("duration")
            sec = int(dur) if isinstance(dur, (int, float)) else avg_seconds

            rows.append((vid, title, up, sec))
            seen.add(vid)
            total += sec

            if total >= target_seconds:
                break
        if total >= target_seconds:
            break

    if not rows:
        print("[!] No results. Check yt-dlp or your queries.", file=sys.stderr)
        sys.exit(2)

    urls_path, m3u_path = write_outputs(args.output_prefix, rows)

    # Optional jingles mix
    jingled = None
    if args.jingles_dir and args.jingle_period and args.jingle_period > 0:
        jingled = write_with_jingles(m3u_path, args.jingles_dir, args.jingle_period)

    print(f"[✓] Tracks: {len(rows)} | Total ~ {human_time(total)}")

    # Optional: play
    if args.play:
        playlist_to_play = jingled if jingled else (m3u_path)
        shuffle = "" if args.no_shuffle else "--shuffle"
        # Pass match-filter to mpv’s ytdl hook to avoid lives
        raw = f'--ytdl-raw-options=match-filter={shlex.quote(match_filter)}'
        cmd = f'{shlex.quote(args.mpv)} --no-video --loop-playlist=inf {shuffle} {raw} --playlist={shlex.quote(str(playlist_to_play))}'
        print(f"[*] Launching mpv:\n{cmd}")
        os.system(cmd)

if __name__ == "__main__":
    main()
