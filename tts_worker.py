#!/usr/bin/env python3
import os, hashlib, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
EL_API  = os.getenv("ELEVENLABS_API_KEY","")
VOICEID = os.getenv("ELEVENLABS_VOICE_ID","")
OUTDIR  = Path("cache/tts")
OUTDIR.mkdir(parents=True, exist_ok=True)

def tts_to_file(text:str, voice_id:str=None, fmt:str="mp3")->Path:
    voice = voice_id or VOICEID
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    out = OUTDIR / f"host_{h}.{fmt}"
    if out.exists():
        return out
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
    headers = {
        "xi-api-key": EL_API,
        "accept": "audio/mpeg" if fmt=="mp3" else "audio/wav",
        "Content-Type": "application/json"
    }
    body = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.85, "style": 0.4, "use_speaker_boost": True}
    }
    with requests.post(url, headers=headers, json=body, stream=True, timeout=90) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            for chunk in r.iter_content(chunk_size=524288):
                if chunk:
                    f.write(chunk)
    return out

if __name__ == "__main__":
    p = tts_to_file("This is Qualisys FM. Coding by day, vibes by night.")
    print(p)
