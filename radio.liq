# radio.liq — minimal, works on Windows
set("init.daemon", false)
set("log.level", 3)
set("frame.size", 4096)

# Reload the playlist every 5 seconds (must be INT)
pl = playlist(mode="normal", reload=5, "radio.m3u")

# Simple crossfade with defaults (no named params)
radio = crossfade(pl)

# --- Windows output (pick ONE) ---
output.portaudio(radio)   # usually works on Windows
# output.winmm(radio)     # fallback if portaudio not present
