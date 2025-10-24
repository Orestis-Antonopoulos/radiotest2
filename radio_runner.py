#!/usr/bin/env python3
# radio_runner.py — Rolling downloader/player with jingles + skip + tiny disk use.
# - Reads URLs from urls.txt (or --urls)
# - Optionally shuffles once (no repeats until list ends)
# - Prefetch-downloads NEXT while playing CURRENT (fast handoffs)
# - Plays via mpv; press 'q' in mpv to skip current track
# - Deletes previous song file right after the next begins (unless --no-delete)
# - Inserts a random jingle every N songs (never deleted)
# - Uses yt-dlp direct bestaudio by default (no re-encode; fastest & robust)
# - Can force extraction (mp3/m4a) with --extract (slower, uses ffmpeg)
#
# Requirements: yt-dlp, mpv, (ffmpeg only if --extract).
# Works great in WSL/Linux.

import argparse
import concurrent.futures as cf
import random
import re
import signal
import subprocess
import sys
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

def mpv_play(mpv_path: str, fpath: Path, quiet: bool = False) -> int:
    # Press 'q' within mpv to end current track (runner advances to next)
    log(f"[►] Playing: {fpath.name}", quiet)
    try:
        return subprocess.call([mpv_path, "--no-video", "--really-quiet", str(fpath)])
    except KeyboardInterrupt:
        raise

def ytdlp_download_direct(ytdlp: str, url: str, cachedir: Path,
                          extra_args: str = "", quiet: bool = False) -> Optional[Path]:
    """
    Fast path: grab bestaudio (no transcoding).
    Returns final file path using yt-dlp's printed 'after_move:filepath'.
    """
    cachedir.mkdir(parents=True, exist_ok=True)
    template = str(cachedir / "%(title).200s [%(id)s].%(ext)s")
    cmd = [
        ytdlp, "--no-playlist",
        "-f", "bestaudio/best",
        "-o", template,
        "--no-part",
        "--print", "after_move:filepath",
        url
    ]
    if extra_args:
        import shlex
        cmd += shlex.split(extra_args)
    log(f"[*] Downloading (direct): {url}", quiet)
    p = run(cmd)
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
    log(f"[✓] Downloaded -> {out.name}", quiet)
    return out

def ytdlp_download_extract(ytdlp: str, url: str, cachedir: Path,
                           audio_format: str, audio_quality: str,
                           extra_args: str = "", quiet: bool = False) -> Optional[Path]:
    """
    Extract path: transcode to mp3/m4a/etc. (slower; requires ffmpeg).
    Returns final file path using printed 'after_move:filepath'.
    """
    cachedir.mkdir(parents=True, exist_ok=True)
    template = str(cachedir / "%(title).200s [%(id)s].%(ext)s")
    cmd = [
        ytdlp, "--no-playlist",
        "-x", "--audio-format", audio_format, "--audio-quality", audio_quality,
        "-o", template,
        "--no-part",
        "--print", "after_move:filepath",
        url
    ]
    if extra_args:
        import shlex
        cmd += shlex.split(extra_args)
    log(f"[*] Downloading (extract:{audio_format}): {url}", quiet)
    p = run(cmd)
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
    log(f"[✓] Downloaded -> {out.name}", quiet)
    return out

def download_audio(ytdlp: str, url: str, cachedir: Path,
                   audio_format: str, audio_quality: str,
                   direct: bool, extra_args: str, quiet: bool) -> Optional[Path]:
    if direct:
        return ytdlp_download_direct(ytdlp, url, cachedir, extra_args, quiet)
    return ytdlp_download_extract(ytdlp, url, cachedir, audio_format, audio_quality, extra_args, quiet)

def main():
    ap = argparse.ArgumentParser(
        description="Rolling downloader/player with jingles (final).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    ap.add_argument("--urls", default="urls.txt", help="Input list of YouTube URLs (one per line)")
    ap.add_argument("--cache-dir", default="cache", help="Folder for downloaded tracks (rolling)")
    ap.add_argument("--jingles-dir", default="jingles_mp3", help="Folder with jingle files")
    ap.add_argument("--jingle-period", type=int, default=2, help="Play one random jingle every N songs (0=off)")
    ap.add_argument("--ytdlp", default=YTDLP_DEFAULT, help="Path to yt-dlp")
    ap.add_argument("--ytdlp-args", default="", help="Extra args for yt-dlp (quoted). E.g. '--cookies cookies.txt --extractor-args youtube:player_client=android'")
    ap.add_argument("--mpv", default=MPV_DEFAULT, help="Path to mpv")
    ap.add_argument("--shuffle", action="store_true", help="Shuffle the URL list once at start")
    ap.add_argument("--audio-format", default="m4a", choices=["mp3","m4a","wav","flac"], help="Target format if --extract is used")
    ap.add_argument("--audio-quality", default="0", help="yt-dlp --audio-quality (0=best, 5=mid, 9=worst)")
    ap.add_argument("--extract", action="store_true", help="Transcode to --audio-format (slower). Default is direct bestaudio (fast)")
    ap.add_argument("--no-delete", action="store_true", help="Do not delete previous songs (debug)")
    ap.add_argument("--verbose", action="store_true", help="Louder logs")
    ap.add_argument("--quiet", action="store_true", help="Minimal logs")
    args = ap.parse_args()

    quiet = args.quiet
    if args.verbose:
        quiet = False

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
            download_audio,
            args.ytdlp, u, cachedir,
            args.audio_format, args.audio_quality,
            (not args.extract),  # direct if no --extract
            args.ytdlp_args, quiet
        )

    idx = 0
    prev_song: Optional[Path] = None
    play_count = 0

    # Kick off first download
    dl_future: Optional[cf.Future] = submit_download(urls[idx])

    try:
        while not stopping and idx < len(urls):
            # Wait for current to finish downloading
            cur_path = dl_future.result() if dl_future else None

            if cur_path is None:
                # Failed download: advance to next URL (do NOT loop forever)
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
            rc = mpv_play(args.mpv, cur_path, quiet)
            play_count += 1

            # Every N songs, play a random jingle (never deleted)
            if args.jingle_period > 0 and (play_count % args.jingle_period == 0) and jingles:
                jingle = random.choice(jingles)
                log(f"[♪] JINGLE: {jingle.name}", quiet)
                _ = mpv_play(args.mpv, jingle, quiet)

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
