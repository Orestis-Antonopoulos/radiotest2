#!/usr/bin/env python3
import os, json, requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
STATION_NAME   = os.getenv("STATION_NAME", "Qualisys FM")

SYS = (
"You are “Mister Q”, a punchy radio host for a dev-friendly station. "
"English. Keep lines short, pronounceable, swagger but no cringe. "
"Max ~12 seconds total. No emojis, no URLs, no brand claims, no lists."
)

def craft_host_text(song_title:str, song_artist:str, headline:str, now_local:str)->dict:
    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role":"system","content": SYS},
            {"role":"user","content": json.dumps({
                "now_local": now_local,
                "station": STATION_NAME,
                "song": {"title": song_title, "artist": song_artist},
                "news": {"headline": headline}
            })}
        ],
        "response_format": {"type":"json_object"},
        "temperature": 0.6,
        "max_tokens": 200
    }
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json=payload, timeout=60
    )
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    data = json.loads(txt)
    # Normalize keys
    return {
        "lead_in": data.get("lead_in",""),
        "news_bite": data.get("news_bite",""),
        "outro": data.get("outro",""),
    }

if __name__ == "__main__":
    # Quick manual test
    print(craft_host_text("Stumblin' In", "CYRIL", "NVIDIA hits new AI milestone.", datetime.now().isoformat()))
