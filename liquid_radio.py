#!/usr/bin/env python3
# liquid_radio.py — tiny: download -> append to M3U -> sprinkle jingles, with logs + fallback
import argparse, random, shlex, subprocess, sys
from pathlib import Path
from typing import List, Optional

DEF_YTDLP = "yt-dlp"

def run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)

def dl_extract(ytdlp: str, url: str, outdir: Path, extra: List[str]) -> Optional[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    tpl = str(outdir / "%(title).200s [%(id)s].%(ext)s")
    cmd = [ytdlp, "--no-playlist",
           "-x","--audio-format","m4a","--audio-quality","0",
           "--match-filter","!is_live & !was_live & duration <= 1200",
           "-o", tpl, "--no-part",
           "--print","after_move:filepath"] + extra + [url]
    p = run(cmd)
    if p.returncode != 0 or not p.stdout.strip():
        sys.stderr.write(f"[yt-dlp extract FAIL] {url}\nSTDERR:\n{p.stderr}\n")
        return None
    pth = Path(p.stdout.strip().splitlines()[-1])
    return pth if pth.exists() else None

def dl_direct(ytdlp: str, url: str, outdir: Path, extra: List[str]) -> Optional[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    tpl = str(outdir / "%(title).200s [%(id)s].%(ext)s")
    cmd = [ytdlp, "--no-playlist",
           "-f","bestaudio/best",
           "--match-filter","!is_live & !was_live & duration <= 1200",
           "-o", tpl, "--no-part",
           "--print","after_move:filepath"] + extra + [url]
    p = run(cmd)
    if p.returncode != 0 or not p.stdout.strip():
        sys.stderr.write(f"[yt-dlp direct FAIL] {url}\nSTDERR:\n{p.stderr}\n")
        return None
    pth = Path(p.stdout.strip().splitlines()[-1])
    return pth if pth.exists() else None

def download(ytdlp: str, url: str, outdir: Path, extra: List[str]) -> Optional[Path]:
    # 1) try extract (m4a); 2) fallback to direct bestaudio
    p = dl_extract(ytdlp, url, outdir, extra)
    if p: return p
    return dl_direct(ytdlp, url, outdir, extra)

def main():
    ap = argparse.ArgumentParser("tiny radio runner")
    ap.add_argument("--urls", default="urls.txt")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--jingles", default="jingles_mp3")
    ap.add_argument("--period", type=int, default=3, help="play 1 jingle after N songs (0=off)")
    ap.add_argument("--m3u", default="radio.m3u")
    ap.add_argument("--ytdlp", default=DEF_YTDLP)
    ap.add_argument("--ytdlp-arg", action="append", default=[],
                    help="repeatable yt-dlp arg, e.g. --ytdlp-arg=--cookies-from-browser --ytdlp-arg=chrome")
    args = ap.parse_args()

    urls_path = Path(args.urls)
    if not urls_path.exists():
        sys.stderr.write(f"[!] missing {urls_path}\n"); sys.exit(2)
    urls = [u.strip() for u in urls_path.read_text(encoding="utf-8").splitlines() if u.strip()]
    if not urls:
        sys.stderr.write("[!] no urls\n"); sys.exit(2)

    jingles_dir = Path(args.jingles)
    jingles = [p for p in jingles_dir.rglob("*") if p.suffix.lower() in (".mp3",".m4a",".flac",".wav")] if jingles_dir.exists() else []
    if args.period>0 and not jingles:
        sys.stderr.write("[i] no jingles found; disabling\n"); args.period=0

    m3u = Path(args.m3u)
    if not m3u.exists():
        m3u.write_text("#EXTM3U\n", encoding="utf-8")

    song_count = 0
    for u in urls:
        fp = download(args.ytdlp, u, Path(args.cache), args.ytdlp_arg)
        if not fp:
            sys.stderr.write(f"[!] skip failed: {u}\n")
            continue
        with m3u.open("a", encoding="utf-8") as f:
            f.write(f"#EXTINF:-1,{fp.stem}\n{fp.as_posix()}\n")
        song_count += 1
        print(f"[+] queued: {fp.name}")

        if args.period>0 and song_count % args.period == 0 and jingles:
            j = random.choice(jingles)
            with m3u.open("a", encoding="utf-8") as f:
                f.write(f"#EXTINF:-1,{j.stem}\n{j.as_posix()}\n")
            print(f"[♪] jingle: {j.name}")

if __name__ == "__main__":
    main()
