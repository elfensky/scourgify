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

from scourgify.common import HERE, GuardrailError

ERR_TRUNC = 140   # chars kept when recording an engine error (same width in bakeoff table and failures CSV)

# $/MTok (input, output) for each engine's default model — public list prices as of 2026-07; edit when they change.
PRICING = {"apple": (0.0, 0.0), "claude": (1.00, 5.00), "openai": (0.15, 0.60), "gemini": (0.30, 2.50), "mistral": (0.20, 0.60)}

# What each engine is LIKE — callers consult these instead of string-testing the name, so adding
# an engine is a row here, not a hunt for `== "apple"` scattered across the tools.
# (Free-ness is a PRICING fact — an engine is free iff its list price is (0, 0) — not a trait row.)
# `out_tokens`: billed OUTPUT tokens per classify call — the estimate est_cost prices. A reasoning
# model bills its hidden thinking as output, so this is NOT just the visible answer: measured against
# real library books (2026-07-30, 5-book sample), gemini-2.5-flash returns ~50 answer tokens on top of
# ~1061 THINKING tokens. Assuming the visible 80 made the wizard quote gemini at a fifth of its real
# price right before the user spends money. Re-measure when a default model changes.
# `role` (a settings row's sub-label) and `limits` (the picker's "how this engine will let you down"
# sentence) are TRAITS rather than UI strings on purpose: the plugin's settings dialog and engine
# picker DERIVE every row from here, so a capability claim a surface wants to make has to become a
# row first. `refuses` is the one the menu reads as behaviour, not prose — only a refusal-class
# failure earns B1's "Retry on <other engine>" (see classify_error below).
_TRAIT_DEFAULTS = {"parallel": True, "judge": True, "hint": "key set ✓", "unusable": "no API key in env",
                   "out_tokens": 80, "refuses": False, "role": "", "limits": ""}
TRAITS = {"apple": {"parallel": False,               # one subprocess pipe — not thread-safe
                    "judge": False,                  # too weak for promote's adversarial refereeing
                    "hint": "free, on-device", "unusable": "needs the afm binary or a swift toolchain",
                    "role": "on-device",
                    "limits": "Single-threaded — a handful of books is fine, thousands take hours. "
                              "Weakest tagging, and cannot judge new tags."},
          "openai": {"role": "cheapest usable",
                     "limits": "Cheapest engine that is actually good here. No content refusals observed."},
          "mistral": {"role": "cheap, untested here",
                      "limits": "Never tested against this library — quality unknown."},
          "claude": {"role": "best judge for new tags",
                     "limits": "Best judge for new-tag candidates. Several times OpenAI's price for "
                               "ordinary tagging."},
          "gemini": {"out_tokens": 1111,             # ~50 answer + ~1061 thinking (measured)
                     "refuses": True,
                     "role": "refuses mature content",
                     "limits": "Refuses mature content — 1 book in 7 on this library, measured. Bills "
                               "~1,061 hidden reasoning tokens per book, so it costs MORE than claude "
                               "despite a lower headline price."}}


def trait(e: str, k: str):
    """One engine's trait, falling back to the cloud-engine defaults for unlisted engines."""
    return TRAITS.get(e, {}).get(k, _TRAIT_DEFAULTS[k])


def is_free(e: str) -> bool:
    """True iff the engine's list price is (0, 0) — the spend gate and cost confirms key off this."""
    return PRICING.get(e) == (0.0, 0.0)


def max_workers(e: str, requested: int) -> int:
    """The concurrency the engine can actually take (a non-parallel engine caps at 1)."""
    return requested if trait(e, "parallel") else 1


def _post_json(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    """POST JSON, return the decoded JSON response — the one transport seam for every cloud engine."""
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    return json.load(urllib.request.urlopen(req, timeout=timeout))


class Apple:
    def __init__(self, model, timeout, env=None):     # env: on-device, no key — accepted so every
        exe = f"{HERE}/afm" if os.path.exists(f"{HERE}/afm") else None   # engine constructs alike
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

    def __init__(self, model, timeout, env=None):
        self.key = (os.environ if env is None else env).get(self.ENV)
        if not self.key: raise GuardrailError(f"{self.NAME} engine needs {self.ENV}{self.KEY_HINT}.")
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

    def __init__(self, model, timeout, env=None):
        env = os.environ if env is None else env
        self.key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
        if not self.key: raise GuardrailError("gemini engine needs GEMINI_API_KEY (or GOOGLE_API_KEY).")
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


def resolve_keys(stored: dict | None = None, env=None) -> dict:
    """{ENV_VAR: key} for every cloud engine that has one — the mapping to hand `usable_engines(env=)`
    and every engine constructor's `env=`. `stored` is {engine: key} from a settings store.

    **The environment WINS over stored** (NLSpec B5.2), which is the opposite of the library path's
    rule (`common.set_library` beats $CALIBRE_LIBRARY) and deliberately so: a key is user config, so
    a scripted or CI run that exports one must keep working unchanged, while the library path is a
    fact about the host process. Nothing here mutates os.environ, so two jobs and a CLI running
    alongside can never observe each other's keys."""
    env = os.environ if env is None else env
    out = {}
    for e, names in ENGINE_ENV.items():
        val = next((env[n] for n in names if env.get(n)), None) or (stored or {}).get(e)
        if val: out[names[0]] = val          # names[0] is the name every constructor reads first
    return out


def key_source(e: str, stored: dict | None = None, env=None) -> str:
    """"env" | "stored" | "" — where this engine's key comes from, so a settings row can SAY that
    the environment is winning instead of silently ignoring what the user typed."""
    env = os.environ if env is None else env
    if any(env.get(n) for n in ENGINE_ENV.get(e, ())): return "env"
    return "stored" if (stored or {}).get(e) else ""


def mask(key: str) -> str:
    """A key rendered for display and NOTHING else: head, bullets, last 4. Short enough to be
    unusable, long enough to tell two keys apart. No caller may render a raw key (B5 postcondition)."""
    if not key: return ""
    return key[:8] + "•" * 12 + key[-4:] if len(key) > 16 else "•" * len(key)


def unmask(typed: str, stored: str) -> str:
    """What a settings field's text MEANS -> the key to store. The mask left untouched keeps the
    stored key; empty clears it; anything else is a new key. Without this the obvious editable field
    saves the literal bullets as the key the first time someone clicks OK without retyping."""
    typed = (typed or "").strip()
    return stored if typed == mask(stored) else typed


def usable_engines(env=None) -> list:
    """Engines runnable here right now: apple needs the afm binary or a swift toolchain, cloud
    engines a key. `env` defaults to os.environ — tests pass a dict instead of juggling it."""
    import shutil
    env = os.environ if env is None else env
    out = []
    for e in ENGINES:
        if not ENGINE_ENV.get(e):                 # no key env = on-device: needs the local runtime
            if os.path.exists(f"{HERE}/afm") or shutil.which("swift"): out.append(e)
        elif any(env.get(k) for k in ENGINE_ENV[e]): out.append(e)
    return out


# The normalized failure taxonomy (NLSpec B3.6). It exists because the GUI derives a RECOVERY VERB
# from it: only a refusal is another engine's problem, so only a refusal earns B1's "Retry on
# <engine>" — a 401 retried on gemini is the same 401 plus a wasted click. `f"{type(e).__name__}: {e}"`
# could not tell those apart, which is why this is a function and not a string.
REFUSAL, AUTH, PERMISSION, QUOTA, TIMEOUT, PARSE, ERROR = (
    "refusal", "auth", "permission", "quota", "timeout", "parse", "error")
CLASSES = (REFUSAL, AUTH, PERMISSION, QUOTA, TIMEOUT, PARSE, ERROR)
# Retrying a bad key or a blocked prompt just spends 14 s of backoff arriving at the same answer.
RETRYABLE = frozenset({QUOTA, TIMEOUT, PARSE, ERROR})


def classify_error(exc: BaseException) -> str:
    """One of CLASSES for an engine exception. urllib raises HTTPError carrying `.code`, which is
    where auth/permission/quota actually live; a RuntimeError is this module's own convention for a
    deterministic content block (Gemini's blocked:/nocontent:)."""
    import json as _json
    import urllib.error
    if isinstance(exc, RuntimeError): return REFUSAL
    if isinstance(exc, urllib.error.HTTPError):
        return {401: AUTH, 403: PERMISSION, 429: QUOTA,
                408: TIMEOUT, 504: TIMEOUT}.get(exc.code, ERROR)
    if isinstance(exc, TimeoutError): return TIMEOUT           # socket.timeout is an alias since 3.10
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, (TimeoutError, OSError)) \
            and "timed out" in str(exc.reason): return TIMEOUT
    if isinstance(exc, (KeyError, IndexError, TypeError, _json.JSONDecodeError)): return PARSE
    return ERROR


def failure_class(reason: str) -> str:
    """The class back out of a recorded failure reason. Rows written before this taxonomy existed
    carry no prefix and read as ERROR — unknown, deliberately NOT refusal, so an old row never gets
    offered a cross-engine retry it was never classified for."""
    head = (reason or "").split(":", 1)[0].strip()
    return head if head in CLASSES else ERROR


def redact(msg: str, *secrets) -> str:
    """Strip API keys out of anything about to be recorded. An HTTPError's message can echo request
    context, and a failure reason ends up in a CSV, a job log and an error dialog — NLSpec B5
    postcondition: no key ever appears in any of them. The length floor keeps an empty or toy
    secret from blanking a whole message; real keys are all far longer."""
    for s in secrets:
        if s and len(s) >= 8: msg = msg.replace(s, "<key>")
    return msg


def ask_retry(eng, prompt: str, tries: int = 4) -> tuple:
    """Call eng.ask(prompt) with backoff. -> (text, "") on success; ("", reason) on failure.

    The reason is PREFIXED with its normalized class ("auth: HTTPError: HTTP Error 401: …") so the
    one shared `reason` column carries the taxonomy without a new column in the CLI-shared failures
    CSV — read it back with `failure_class()`, never by hand-splitting. Only RETRYABLE classes get
    the 2**k backoff."""
    key = getattr(eng, "key", None)
    err = ""
    for k in range(tries):
        try: return eng.ask(prompt), ""
        except Exception as e:
            cls = classify_error(e)
            err = redact(f"{cls}: {type(e).__name__}: {e}", key)[:ERR_TRUNC]
            if cls not in RETRYABLE or k == tries - 1: return "", err
            time.sleep(2 ** k)
    return "", err
