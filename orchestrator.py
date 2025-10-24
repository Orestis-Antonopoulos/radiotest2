#!/usr/bin/env python3
# Orchestrator: plays [HOST TTS] -> [SONG], jingle every N tracks, rolling delete.
import os, re, json, random, argparse, subprocess, time
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# --- simple helpers reused from runner ---
import subprocess

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def mpv_play(path:Path)->int:
    # Press 'q' to skip current and continue
    return subprocess.call(["mpv","--no-video","--really-quiet",str(path)])

def ytdlp_download_direct(ytdlp, url, cachedir, extra_args=""):
    cachedir.mkdir(parents=True, exist_ok=True)
    template = str(cachedir / "%(title).200s [%(id)s].%(ext)s")
    cmd = [ytdlp, "--no-playlist", "-f","bestaudio/best","-o",template,"--no-part","--print","after_move:filepath", url]
    if extra_args:
        import shlex; cmd += shlex.split(extra_args)
    p = run(cmd)
    if p.returncode != 0:
        print(f"[!] yt-dlp failed: {url}\nSTDERR:\n{p.stderr}\nSTDOUT:\n{p.stdout}")
        return None
    line = (p.stdout or "").strip().splitlines()[-1] if p.stdout else ""
    out = Path(line) if line else None
    if not out or not out.exists():
        print(f"[!] download reported but file missing for {url}")
        return None
    print(f"[✓] Downloaded -> {out.name}")
    return out

# --- load workers ---
from host_worker import craft_host_text
from tts_worker import tts_to_file

# --- main ---
def parse_title_artist(title:str):
    # Try: "Artist - Title (Official ...)"
    if " - " in title:
        a, b = title.split(" - ", 1)
        # strip brackets/parentheses
        b = re.sub(r"\s*[\[\(].*?[\]\)]\s*", "", b).strip()
        return b or title, a.strip()
    return title, ""

def load_headlines():
    p = Path("cache/news.json")
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [f"{x.get('title','')}" for x in data.get("items",[])]
    except Exception:
        return []

def main():
    ap = argparse.ArgumentParser(description="Qualisys FM Orchestrator (between-tracks mode)")
    ap.add_argument("--urls", default="urls.txt", help="Input list of YouTube URLs")
    ap.add_argument("--cache-dir", default="cache/tracks", help="Folder for rolling audio")
    ap.add_argument("--jingles-dir", default="jingles_mp3", help="Folder with jingles")
    ap.add_argument("--jingle-period", type=int, default=2, help="Play a random jingle every N songs (0=off)")
    ap.add_argument("--ytdlp", default="yt-dlp", help="Path to yt-dlp")
    ap.add_argument("--ytdlp-args", default="", help="Extra args for yt-dlp (quoted)")
    ap.add_argument("--shuffle", action="store_true", help="Shuffle once at start")
    ap.add_argument("--no-delete", action="store_true", help="Keep downloaded songs (debug)")
    args = ap.parse_args()

    urls_path = Path(args.urls)
    if not urls_path.exists():
        print(f"[!] Missing {urls_path}"); return

    urls = [ln.strip() for ln in urls_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if args.shuffle:
        random.shuffle(urls)

    jingles = []
    jroot = Path(args.jingles_dir)
    if args.jingle_period>0 and jroot.exists():
        jingles = [p for p in jroot.rglob("*") if p.suffix.lower() in (".mp3",".m4a",".wav",".flac")]
        print(f"[i] Loaded {len(jingles)} jingles.")
    headlines = load_headlines()

    cachedir = Path(args.cache_dir); cachedir.mkdir(parents=True, exist_ok=True)

    prev_song = None
    count = 0

    for url in urls:
        # 1) Download next track
        song_path = ytdlp_download_direct(args.ytdlp, url, cachedir, args.ytdlp_args)
        if song_path is None:
            continue

        # 2) Prep host segment (short + one news bite)
        #    Extract quick artist/title from the filename if needed
        base = song_path.stem  # "... [ID]"
        raw_title = re.sub(r"\s\[[A-Za-z0-9_\-]{8,}\]$", "", base).strip()
        title, artist = parse_title_artist(raw_title)
        headline = random.choice(headlines) if headlines else ""
        now_local = datetime.now().strftime("%Y-%m-%d %H:%M")
        host = craft_host_text(title, artist, headline, now_local)
        host_text = " ".join([host.get("lead_in",""), host.get("news_bite",""), host.get("outro","")]).strip()
        if not host_text:
            host_text = f"You’re on {os.getenv('STATION_NAME','Qualisys FM')}. Up next: {artist} — {title}."

        tts_file = tts_to_file(host_text)

        # 3) Play HOST, then SONG
        print(f"[HOST] {host_text}")
        mpv_play(tts_file)
        print(f"[SONG] {raw_title}")
        rc = mpv_play(song_path)
        count += 1

        # 4) Jingle every N songs
        if args.jingle_period>0 and (count % args.jingle_period == 0) and jingles:
            j = random.choice(jingles)
            print(f"[♪] JINGLE: {j.name}")
            mpv_play(j)

        # 5) Rolling delete
        if not args.no_delete and prev_song and prev_song.exists():
            try:
                prev_song.unlink()
                print(f"[x] Deleted previous: {prev_song.name}")
            except Exception as e:
                print(f"[!] Could not delete {prev_song}: {e}")
        prev_song = song_path

    print(f"[✓] Show complete. Played {count} tracks.")

if __name__ == "__main__":
    main()
