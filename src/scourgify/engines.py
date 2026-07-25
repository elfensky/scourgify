#!/usr/bin/env python3
"""The LLM engine seam, shared by classify / promote / the wizard. One module owns which engines
exist (ENGINES), which env key powers each (ENGINE_ENV), list pricing (PRICING), availability
(usable_engines) and the retry policy (ask_retry) — so the tools and the wizard can never drift.
`_post_json` is the single HTTP transport: tests monkeypatch it instead of the network, and the
cloud adapters differ only by their constants (URL / env var / default model / extract path)."""
import json
import os
import subprocess
import time

from scourgify.common import HERE

ERR_TRUNC = 140   # chars kept when recording an engine error (same width in bakeoff table and failures CSV)

# $/MTok (input, output) for each engine's default model — public list prices as of 2026-07; edit when they change.
PRICING = {"apple": (0.0, 0.0), "claude": (1.00, 5.00), "openai": (0.15, 0.60), "gemini": (0.30, 2.50), "mistral": (0.20, 0.60)}


def _post_json(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    """POST JSON, return the decoded JSON response — the one transport seam for every cloud engine."""
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    return json.load(urllib.request.urlopen(req, timeout=timeout))


class Apple:
    def __init__(self, model, timeout):
        exe = f"{HERE}/afm" if os.path.exists(f"{HERE}/afm") else None
        cmd = [exe] if exe else ["swift", f"{HERE}/afm.swift"]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def ask(self, prompt):
        self.p.stdin.write(prompt.replace("\n", "") + "\n"); self.p.stdin.flush()
        return self.p.stdout.readline()


class _Chat:
    """OpenAI-style chat-completions adapter — concrete engines vary only by the constants
    (and Claude by its header/extract overrides)."""
    NAME = URL = ENV = DEFAULT = ""
    KEY_HINT = ""    # appended to the missing-key message

    def __init__(self, model, timeout):
        self.key = os.environ.get(self.ENV)
        if not self.key: raise SystemExit(f"{self.NAME} engine needs {self.ENV}{self.KEY_HINT}.")
        self.model = model or self.DEFAULT; self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "content-type": "application/json"}

    def _extract(self, r: dict) -> str:
        return r["choices"][0]["message"]["content"]

    def ask(self, prompt):
        payload = {"model": self.model, "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]}
        return self._extract(_post_json(self.URL, self._headers(), payload, self.timeout))


class Claude(_Chat):
    NAME, ENV, DEFAULT = "claude", "ANTHROPIC_API_KEY", "claude-haiku-4-5-20251001"
    URL = "https://api.anthropic.com/v1/messages"
    KEY_HINT = " (or use --engine apple)"

    def _headers(self):
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def _extract(self, r):
        return r["content"][0]["text"]


class OpenAI(_Chat):
    NAME, ENV, DEFAULT = "openai", "OPENAI_API_KEY", "gpt-4o-mini"
    URL = "https://api.openai.com/v1/chat/completions"


class Mistral(_Chat):
    NAME, ENV, DEFAULT = "mistral", "MISTRAL_API_KEY", "mistral-small-latest"
    URL = "https://api.mistral.ai/v1/chat/completions"


class Gemini:
    # personal fanfic library: don't let safety filters drop mature/dark stories (the tag list itself lists such terms)
    SAFE = [{"category": c, "threshold": "BLOCK_NONE"} for c in
            ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
             "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]

    def __init__(self, model, timeout):
        self.key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.key: raise SystemExit("gemini engine needs GEMINI_API_KEY (or GOOGLE_API_KEY).")
        self.model = model or "gemini-2.5-flash"; self.timeout = timeout

    def ask(self, prompt):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {"contents": [{"parts": [{"text": prompt}]}], "safetySettings": self.SAFE,
                   "generationConfig": {"maxOutputTokens": 2048}}      # bound cost; roomy for thinking models
        r = _post_json(url, {"content-type": "application/json", "x-goog-api-key": self.key},   # key in header, never in the URL
                       payload, self.timeout)
        cands = r.get("candidates")
        if not cands: raise RuntimeError("blocked:" + str(r.get("promptFeedback", {}).get("blockReason")))
        parts = cands[0].get("content", {}).get("parts")
        if not parts: raise RuntimeError("nocontent:" + str(cands[0].get("finishReason")))
        return parts[0]["text"]


ENGINES = {"apple": Apple, "claude": Claude, "openai": OpenAI, "gemini": Gemini, "mistral": Mistral}
# env var(s) each cloud engine's key is read from (apple is on-device, no key). Single source of truth —
# wizard.ENGINE_KEYS aliases this so the two can never disagree about which key powers which engine.
ENGINE_ENV = {"claude": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
              "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "mistral": ("MISTRAL_API_KEY",)}


def usable_engines() -> list:
    """Engines runnable here right now: apple needs the afm binary or a swift toolchain, cloud engines a key."""
    import shutil
    out = []
    for e in ENGINES:
        if e == "apple":
            if os.path.exists(f"{HERE}/afm") or shutil.which("swift"): out.append(e)
        elif any(os.environ.get(k) for k in ENGINE_ENV[e]): out.append(e)
    return out


def ask_retry(eng, prompt: str, tries: int = 4) -> tuple:
    """Call eng.ask(prompt) with backoff. -> (text, "") on success; ("", reason) on failure.
    RuntimeError = deterministic content block (no retry); other errors retry with 2**k backoff."""
    err = ""
    for k in range(tries):
        try: return eng.ask(prompt), ""
        except RuntimeError as e:
            return "", str(e)[:ERR_TRUNC]
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:ERR_TRUNC]
            if k == tries - 1: return "", err
            time.sleep(2 ** k)
    return "", err
