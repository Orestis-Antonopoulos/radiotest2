#!/usr/bin/env bash
# radio_prefetch.sh — stream-without-stutter by pre-downloading.
# Usage (examples at bottom).

set -euo pipefail

# ----------- CONFIG (edit or override via env/flags) -----------
# Search query (or provide a static playlist URL below if you prefer)
QUERY="${QUERY:-greek trap 2024 official audio}"

# How many songs between jingles
N="${N:-3}"

# Jingles directory (mp3 or m4a files)
JDIR="${JDIR:-$HOME/code/radiotest/jingles_mp3}"

# Cache dir for downloaded tracks
CACHE="${CACHE:-$HOME/code/radiotest/cache}"

# Path to yt-dlp (use your venv if you have one)
YTDLP="${YTDLP:-$HOME/code/radiotest/.venv/bin/yt-dlp}"

# Optional cookies file (leave empty to skip)
COOKIES="${COOKIES:-$HOME/cookies.txt}"
USE_COOKIES=""
[ -f "$COOKIES" ] && USE_COOKIES=(--cookies "$COOKIES") || USE_COOKIES=()

# yt-dlp filters: no live, <15 minutes
MATCH_FILTER="live_status!='is_live' & duration < 900"

# mpv flags (local file playback is smooth; no WSLg hacks needed)
MPV=(mpv --no-video --quiet --force-seekable=yes)

# ---------------------------------------------------------------

mkdir -p "$CACHE"

# dl() — download ONE track for the given search query; echo file path
dl() {
  local q="$1"
  # pick the first clean result
  local id
  id="$("$YTDLP" --match-filter "$MATCH_FILTER" --print id "ytsearch20:$q" | head -n1 || true)"
  [ -z "${id:-}" ] && return 1

  # download bestaudio to CACHE; safe filenames; no leftover fragments
  "$YTDLP" "${USE_COOKIES[@]}" -f bestaudio --no-playlist --no-keep-fragments \
          --restrict-filenames -o "$CACHE/%(title).80s-%(id)s.%(ext)s" \
          "https://www.youtube.com/watch?v=$id" >/dev/null

  # echo the newest file path
  ls -t "$CACHE" | head -n1 | sed "s|^|$CACHE/|"
}

# jingle() — play one random jingle if folder exists
jingle() {
  [ -d "$JDIR" ] || return 0
  local f
  f="$(find "$JDIR" -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.m4a' \) | shuf -n1 || true)"
  [ -n "${f:-}" ] && "${MPV[@]}" "$f" || true
}

# Prefetch two files up front
echo "Seeding first two tracks for: $QUERY"
NEXT="$(dl "$QUERY")"   || { echo "No result for: $QUERY"; exit 1; }
LATER="$(dl "$QUERY")"  || { echo "Only one result found."; exit 1; }

i=0
while true; do
  CUR="$NEXT"

  # start prefetch of the following in background, write path to temp
  TMP="$(mktemp)"
  ( dl "$QUERY" >"$TMP" 2>/dev/null || true ) & pid=$!

  # play current locally (no stutter)
  "${MPV[@]}" "$CUR" || true

  # delete current
  rm -f -- "$CUR"

  # rotate NEXT from background result (if bad fetch, try once more inline)
  wait "$pid" || true
  NEXT="$(cat "$TMP" 2>/dev/null || true)"
  rm -f "$TMP"
  if [ -z "${NEXT:-}" ]; then
    NEXT="$(dl "$QUERY" || true)"
    [ -z "${NEXT:-}" ] && { echo "Couldn’t fetch a new track. Sleeping 5s..."; sleep 5; continue; }
  fi

  # every N songs → jingle
  i=$((i+1))
  if [ "$i" -ge "$N" ]; then
    i=0
    jingle
  fi
done

# ------------------ USAGE EXAMPLES ------------------
# make executable:
#   chmod +x radio_prefetch.sh
#
# simplest run (uses defaults at top):
#   ./radio_prefetch.sh
#
# override query & jingles (env vars):
#   QUERY="lofi hip hop 2024" JDIR="$HOME/code/radiotest/jingles_mp3" ./radio_prefetch.sh
#
# change jingle interval:
#   N=5 ./radio_prefetch.sh
#
# point to your venv yt-dlp:
#   YTDLP="$HOME/code/radiotest/.venv/bin/yt-dlp" ./radio_prefetch.sh
#
# use cookies if you hit bot-wall (ensure file exists):
#   COOKIES="$HOME/cookies.txt" ./radio_prefetch.sh
#
# keep cache somewhere else:
#   CACHE="$HOME/radio_cache" ./radio_prefetch.sh
#
# ----------------------------------------------------
