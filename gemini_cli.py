#!/usr/bin/env python3
import argparse, sys, os
from pathlib import Path
from google import genai
from google.genai import types

# --- auto-load .env next to this file ---
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"))
except Exception:
    pass

def read_stdin_if_piped() -> str | None:
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        return data.strip() or None
    return None

def load_text_from_file(path: str) -> str:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    return p.read_text(encoding="utf-8")

def build_contents(user_text: str, attachments):
    contents = []
    if attachments:
        contents.extend(attachments)
    contents.append(user_text)
    return contents

def resolve_api_key(cli_key: str | None) -> str:
    if cli_key:
        return cli_key
    for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(k)
        if v and v.strip():
            return v.strip()
    raise RuntimeError("No API key found. Provide --api-key or set GOOGLE_API_KEY or GEMINI_API_KEY.")

def print_with_citations(resp):
    """Print text and any grounded web citations (non-streaming mode only)."""
    txt = getattr(resp, "text", "") or ""
    printed_sources = False
    try:
        cand = resp.candidates[0]
        gm = getattr(cand, "grounding_metadata", None)
        if gm:
            chunks = getattr(gm, "grounding_chunks", []) or []
            links = []
            for i, ch in enumerate(chunks, 1):
                web = getattr(ch, "web", None)
                if web and getattr(web, "uri", None):
                    title = (web.title or web.uri).strip()
                    links.append(f"[{i}] {title} — {web.uri}")
            if links:
                print(txt)
                print("\nSources:")
                for s in links:
                    print(" -", s)
                printed_sources = True
    except Exception:
        pass
    if not printed_sources:
        print(txt)

def main():
    p = argparse.ArgumentParser(description="Gemini CLI — prompts from CLI, files, or stdin. Optional web grounding.")
    p.add_argument("--api-key", help="Override API key (otherwise uses GOOGLE_API_KEY or GEMINI_API_KEY).")
    p.add_argument("--model", default="gemini-2.5-flash", help="Model name (default: gemini-2.5-flash)")
    p.add_argument("--text", "-t", help="Inline prompt text.")
    p.add_argument("--file", "-f", help="Read prompt text from a file (UTF-8).")
    p.add_argument("--stream", action="store_true", help="Stream tokens as they arrive (citations not shown in stream).")
    p.add_argument("--attach", "-a", action="append", help="Attach a file (PDF, txt, image, etc.). Repeatable.")
    p.add_argument("--system", "-s", default=None, help="System instruction (behavior/style guardrails).")
    p.add_argument("--temperature", type=float, default=None, help="Sampling temperature (e.g., 0.2)")
    p.add_argument("--max-output-tokens", type=int, default=None, help="Cap on generated tokens.")
    p.add_argument("--json", action="store_true", help='Ask for JSON: sets response_mime_type="application/json".')
    # NEW: web grounding
    p.add_argument("--web", action="store_true",
                   help="Enable Google Search grounding so the model can look things up and cite sources.")
    p.add_argument("--cite", action="store_true",
               help="Print grounded web citations (only when --web is used).")

    args = p.parse_args()

    # resolve prompt text
    if args.text:
        prompt = args.text
    elif args.file:
        prompt = load_text_from_file(args.file)
    else:
        prompt = read_stdin_if_piped()

    if not prompt:
        print("No prompt provided. Use --text, --file, or pipe via STDIN.", file=sys.stderr)
        sys.exit(2)

    # client
    api_key = resolve_api_key(args.api_key)
    client = genai.Client(api_key=api_key)

    # attachments
    uploaded = []
    if args.attach:
        for path in args.attach:
            uploaded.append(client.files.upload(file=path))

    # request args
    gen_args = {
        "model": args.model,
        "contents": build_contents(prompt, uploaded),
    }

    # generation config
    generation_config = {}
    if args.temperature is not None:
        generation_config["temperature"] = args.temperature
    if args.max_output_tokens is not None:
        generation_config["max_output_tokens"] = args.max_output_tokens
    if args.json:
        generation_config["response_mime_type"] = "application/json"
    if generation_config:
        gen_args["generation_config"] = generation_config

    # system instruction
    if args.system:
        gen_args["system_instruction"] = args.system

    # tools: google search grounding
    tools = []
    if args.web:
        tools.append(types.Tool(google_search=types.GoogleSearch()))
    if tools:
        gen_args["config"] = types.GenerateContentConfig(tools=tools)

    try:
        if args.stream:
            # Stream text only; citations appear only in non-streaming responses.
            stream = client.models.generate_content_stream(**gen_args)
            for chunk in stream:
                if getattr(chunk, "text", None):
                    print(chunk.text, end="", flush=True)
            print()
        else:
            resp = client.models.generate_content(**gen_args)
            if args.web and args.cite:
                print_with_citations(resp)
            else:
                print(getattr(resp, "text", str(resp)))
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
