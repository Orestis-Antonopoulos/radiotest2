#!/usr/bin/env python3
# radio_runner.py — Rolling downloader/player with jingles + skip + tiny disk use.
# - Reads URLs from urls.txt (or --urls)
# - Optionally shuffles once (no repeats until list ends)
# - Prefetch-downloads NEXT while playing CURRENT (fast handoffs)
# - Plays via mpv; press 'q' in mpv to skip current track
# - Deletes previous song file right after the next begins (unless --no-delete)
# - Inserts a random jingle every N songs (never deleted)
# - Uses yt-dlp direct bestaudio by default (no re-encode; fastest & robust)
# - Can force extraction (mp3/m4a) with --extract (slower; uses ffmpeg)
#
# Hardened for:
# - mpv anti-stutter (large cache, no cache-pause)
# - yt-dlp SABR / 416: retries + Android client fallback (+ optional cookies)
#
# Works great on Linux/WSL.

import argparse
import concurrent.futures as cf
import random
import re
import signal
import subprocess
import sys
import shlex
import time
from pathlib import Path
from typing import Optional, List

YTDLP_DEFAULT = "yt-dlp"
MPV_DEFAULT = "mpv"

# Accepts v=, youtu.be/, shorts/
YOUTUBE_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_\-]{8,})")

def log(msg: str, quiet: bool = False):
    if not quiet:
        print(msg, flush=True)

def run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)

def parse_id(url: str) -> Optional[str]:
    m = YOUTUBE_ID_RE.search(url)
    return m.group(1) if m else None

def build_mpv_base_args(quiet: bool) -> List[str]:
    base = [
        "--no-config",
        "--no-video",
        "--cache=yes",
        "--demuxer-readahead-secs=60",
        "--cache-secs=120",
        "--cache-pause=no",
        "--audio-samplerate=48000",
        "--audio-channels=stereo",
        "--audio-buffer=0.5",
        "--keep-open=no",
        "--no-resume-playback",
    ]
    if quiet:
        base.append("--really-quiet")
    return base

def mpv_play(mpv_path: str, fpath: Path, mpv_extra: List[str], quiet: bool = False) -> int:
    log(f"[►] Playing: {fpath.name}", quiet)
    cmd = [mpv_path] + build_mpv_base_args(quiet) + mpv_extra + [str(fpath)]
    return subprocess.call(cmd)

def build_ytdlp_cmd_base(ytdlp: str, template: str, direct: bool,
                         audio_format: str, audio_quality: str) -> List[str]:
    if direct:
        return [
            ytdlp, "--no-playlist",
            "-f", "bestaudio/best",
            "-o", template,
            "--no-part",
            "--print", "after_move:filepath",
        ]
    else:
        return [
            ytdlp, "--no-playlist",
            "-x", "--audio-format", audio_format, "--audio-quality", audio_quality,
            "-o", template,
            "--no-part",
            "--print", "after_move:filepath",
        ]

def try_ytdlp_once(cmd: List[str], url: str, quiet: bool) -> Optional[Path]:
    p = run(cmd + [url])
    if p.returncode != 0:
        log(f"[!] yt-dlp failed: {url}\nSTDERR:\n{p.stderr}\nSTDOUT:\n{p.stdout}", quiet)
        return None
    path = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    if not path:
        log(f"[!] yt-dlp gave no filepath for {url}", quiet)
        return None
    out = Path(path)
    if not out.exists():
        log(f"[!] Download reported but file missing: {out}", quiet)
        return None
    return out

def ytdlp_download_smart(ytdlp: str, url: str, cachedir: Path,
                         direct: bool, audio_format: str, audio_quality: str,
                         user_extra: List[str], quiet: bool) -> Optional[Path]:
    """
    Robust downloader:
      1) Try with user args.
      2) If SABR/416 or signature issues, retry with Android client.
      3) Final attempt with Android + small http-chunk-size.
    """
    cachedir.mkdir(parents=True, exist_ok=True)
    template = str(cachedir / "%(title).200s [%(id)s].%(ext)s")

    base = build_ytdlp_cmd_base(ytdlp, template, direct, audio_format, audio_quality)

    # assemble strategies
    strategies: List[List[str]] = []

    # User-provided first (if any)
    strategies.append(user_extra or [])

    # Android client fallback (bypasses a lot of SABR)
    strategies.append(shlex.split('--extractor-args youtube:player_client=android'))

    # Android + chunking (helps with 416 range weirdness on some networks)
    strategies.append(shlex.split('--extractor-args youtube:player_client=android --http-chunk-size 10M'))

    # Try each strategy
    for i, extra in enumerate(strategies, start=1):
        label = "user-args" if i == 1 else ("android" if i == 2 else "android+chunk")
        log(f"[*] Downloading ({'direct' if direct else 'extract'}:{label}): {url}", quiet)
        out = try_ytdlp_once(base + extra, url, quiet)
        if out:
            log(f"[✓] Downloaded -> {out.name}", quiet)
            return out
        # small backoff before next attempt
        time.sleep(1.0)

    # last resort: if user didn’t supply cookies and still failing, hint them
    log("[!] All strategies failed. Consider adding '--ytdlp-arg --cookies-from-browser chrome' "
        "or exporting a cookies.txt for region/age/SABR problems.", quiet)
    return None

def main():
    ap = argparse.ArgumentParser(
        description="Rolling downloader/player with jingles (final, hardened).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    # Repeatable args to avoid shell quoting hell
    ap.add_argument("--mpv-arg", action="append", default=[],
                    help="Extra arg for mpv (repeatable). E.g. --mpv-arg --ao=alsa")
    ap.add_argument("--ytdlp-arg", action="append", default=[],
                    help="Extra arg for yt-dlp (repeatable). E.g. --ytdlp-arg --cookies-from-browser --ytdlp-arg chrome")
    # Back-compat single-string forms (optional)
    ap.add_argument("--mpv-args", default="", help="Extra args for mpv as a single string")
    ap.add_argument("--ytdlp-args", default="", help="Extra args for yt-dlp as a single string")

    ap.add_argument("--urls", default="urls.txt", help="Input list of YouTube URLs (one per line)")
    ap.add_argument("--cache-dir", default="cache", help="Folder for downloaded tracks (rolling)")
    ap.add_argument("--jingles-dir", default="jingles_mp3", help="Folder with jingle files")
    ap.add_argument("--jingle-period", type=int, default=5, help="Play one random jingle every N songs (0=off)")
    ap.add_argument("--ytdlp", default=YTDLP_DEFAULT, help="Path to yt-dlp")
    ap.add_argument("--mpv", default=MPV_DEFAULT, help="Path to mpv")
    ap.add_argument("--shuffle", action="store_true", help="Shuffle the URL list once at start")
    ap.add_argument("--audio-format", default="m4a", choices=["mp3","m4a","wav","flac"],
                    help="Target format if --extract is used")
    ap.add_argument("--audio-quality", default="0", help="yt-dlp --audio-quality (0=best, 5=mid, 9=worst)")
    ap.add_argument("--extract", action="store_true",
                    help="Transcode to --audio-format (slower). Default is direct bestaudio (fast)")
    ap.add_argument("--no-delete", action="store_true", help="Do not delete previous songs (debug)")
    ap.add_argument("--verbose", action="store_true", help="Louder logs")
    ap.add_argument("--quiet", action="store_true", help="Minimal logs")
    args = ap.parse_args()

    quiet = args.quiet
    if args.verbose:
        quiet = False

    # Merge repeatable + single-string extras
    mpv_extra: List[str] = []
    if args.mpv_arg:
        mpv_extra += args.mpv_arg
    if args.mpv_args:
        mpv_extra += shlex.split(args.mpv_args)

    ytdlp_extra: List[str] = []
    if args.ytdlp_arg:
        ytdlp_extra += args.ytdlp_arg
    if args.ytdlp_args:
        ytdlp_extra += shlex.split(args.ytdlp_args)

    urls_path = Path(args.urls)
    if not urls_path.exists():
        print(f"[!] Missing {urls_path}", file=sys.stderr)
        sys.exit(2)

    urls = [ln.strip() for ln in urls_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not urls:
        print("[!] No URLs found.", file=sys.stderr)
        sys.exit(2)

    if args.shuffle:
        random.shuffle(urls)

    cachedir = Path(args.cache_dir)
    cachedir.mkdir(parents=True, exist_ok=True)

    jroot = Path(args.jingles_dir)
    jingles: List[Path] = []
    if args.jingle_period > 0 and jroot.exists():
        jingles = [p for p in jroot.rglob("*") if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".flac")]
        if not jingles:
            log(f"[!] No audio files in {jroot}. Jingles disabled.", quiet)
            args.jingle_period = 0
    else:
        if args.jingle_period > 0:
            log(f"[!] Jingles dir not found: {jroot} (disabling jingles)", quiet)
            args.jingle_period = 0

    log(f"[i] Using yt-dlp: {args.ytdlp}", quiet)
    log(f"[i] Using mpv   : {args.mpv}", quiet)
    log(f"[i] URLs loaded : {len(urls)}", quiet)
    log(f"[i] Cache dir   : {cachedir.resolve()}", quiet)
    if args.jingle_period > 0:
        log(f"[i] Jingles    : {len(jingles)} files | period: {args.jingle_period}", quiet)

    stopping = False
    def handle_sigint(sig, frame):
        nonlocal stopping
        stopping = True
        print("\n[!] Stopping…", file=sys.stderr)
    signal.signal(signal.SIGINT, handle_sigint)

    executor = cf.ThreadPoolExecutor(max_workers=1)

    def submit_download(u: str):
        return executor.submit(
            ytdlp_download_smart,
            args.ytdlp, u, cachedir,
            (not args.extract),  # direct if no --extract
            args.audio_format, args.audio_quality,
            ytdlp_extra, quiet
        )

    idx = 0
    prev_song: Optional[Path] = None
    play_count = 0

    # Kick off first download
    dl_future: Optional[cf.Future] = submit_download(urls[idx])

    try:
        while not stopping and idx < len(urls):
            cur_path = dl_future.result() if dl_future else None

            if cur_path is None:
                log(f"[!] Skipping failed download: {urls[idx]}", quiet)
                idx += 1
                if idx < len(urls):
                    dl_future = submit_download(urls[idx])
                    continue
                else:
                    break

            # Prefetch next ASAP
            next_future: Optional[cf.Future] = None
            if idx + 1 < len(urls):
                next_future = submit_download(urls[idx + 1])

            # Play current
            rc = mpv_play(args.mpv, cur_path, mpv_extra, quiet)
            play_count += 1

            # Every N songs, play a random jingle (never deleted)
            if args.jingle_period > 0 and (play_count % args.jingle_period == 0) and jingles:
                jingle = random.choice(jingles)
                log(f"[♪] JINGLE: {jingle.name}", quiet)
                _ = mpv_play(args.mpv, jingle, mpv_extra, quiet)

            # Rolling delete: nuke previous file right after next starts
            if not args.no_delete and prev_song and prev_song.exists():
                try:
                    prev_song.unlink()
                    log(f"[x] Deleted previous: {prev_song.name}", quiet)
                except Exception as e:
                    log(f"[!] Could not delete {prev_song}: {e}", quiet)

            prev_song = cur_path

            # Advance
            dl_future = next_future
            idx += 1

            if dl_future is None:
                # No more tracks queued
                break

        log(f"[✓] Done. Played {play_count} song(s).", quiet)
    finally:
        executor.shutdown(wait=False)

if __name__ == "__main__":
    main()
