#!/usr/bin/env python3
"""Content-based tagging from a controlled vocabulary, with TWO outputs per book:
  1) added_tags    — tags chosen from defaults/classify_vocab.txt (the consolidated set); these get APPLIED.
  2) proposed_new  — short reusable tags the model thinks apply but are NOT in the vocab yet; aggregated into
                     classify_newtags_ranked.csv for review, so the vocabulary grows cleanly (promote -> vocab).

  python3 classify.py [--engine apple|claude|openai|gemini] [--incremental] [--workers N] [--batch N] [--fresh]
  python3 classify.py --apply                    # apply 'added_tags' + stamp #wrangled (Calibre CLOSED; writes shell to calibre-debug)

Engines (--engine):  apple = on-device Apple Foundation Models via ./afm (free; macOS 26+).
          claude = Anthropic (ANTHROPIC_API_KEY) | openai = OpenAI (OPENAI_API_KEY) | gemini = Google (GEMINI_API_KEY).
          --model overrides the per-engine default. Only books with < --min-tags tags AND a description are processed.
          Runs are resumable (skip books already in the proposal; --fresh restarts). Dry-run until --apply.
--incremental = cheap maintenance after new downloads: (re)process only books whose #updated is newer than their own
          #wrangled marker (or never wrangled), plus any still untagged. --apply auto-creates the #wrangled datetime
          column and stamps each tagged book, so the state lives IN the library — no external file. (--since DATE
          forces an explicit cutoff against #updated.) Avoids the ~full-library cost of a --fresh pass."""
import os, sys, re, csv, json, sqlite3, subprocess, collections
from contextlib import nullcontext
try:                                              # rich is optional: pretty progress/tables in system python3
    from rich.console import Console
    from rich.progress import Progress, BarColumn, TextColumn, MofNCompleteColumn, TimeRemainingColumn
    from rich.table import Table
    _con = Console(stderr=True); RICH = True
except ImportError:
    RICH = False

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB: raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder.")
DB = os.path.join(LIB, "metadata.db")
def argval(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default
ENGINE = argval("--engine", "apple")
APPLY = "--apply" in sys.argv
LIMIT = int(argval("--limit", "0"))
MIN_TAGS = int(argval("--min-tags", "2"))
MODEL = argval("--model", "")            # override per-engine default model
BATCH = int(argval("--batch", "0"))      # process only N new books per run (0 = all); re-run resumes
WORKERS = int(argval("--workers", "8"))  # concurrent API requests (cloud engines are I/O-bound)
if ENGINE == "apple": WORKERS = 1        # apple = one subprocess pipe, not thread-safe
MAXTAGS = int(argval("--max-tags", "6"))
TIMEOUT = int(argval("--timeout", "60"))   # per-request HTTP timeout (s) so a hung call can't stall a worker

VOCAB = [l.strip() for l in open(f"{HERE}/defaults/classify_vocab.txt") if l.strip() and not l.startswith("#")]
VLOW = {v.lower(): v for v in VOCAB}

def prompt_for(desc):
    return ("You are tagging a fanfiction story. Return ONLY a JSON object with two arrays:\n"
            f'  "tags": tags from the CONTROLLED LIST below that clearly apply (exact spelling, at most {MAXTAGS}; '
            "be conservative; [] if vague; do NOT echo the whole list).\n"
            '  "new": up to 3 SHORT reusable trope/genre/theme tags (Title Case) that clearly apply but are NOT in the '
            "list and would be worth adding to the vocabulary. No plot specifics, character names, or fandoms; [] if none.\n"
            f"CONTROLLED LIST: {', '.join(VOCAB)}\n\nDESCRIPTION:\n{desc[:1500]}\n\nJSON:")

def parse_resp(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m: return [], []
    try: obj = json.loads(m.group(0))
    except Exception: return [], []
    vt = [VLOW[str(t).strip().lower()] for t in obj.get("tags", []) if str(t).strip().lower() in VLOW]
    if len(vt) > MAXTAGS * 2: vt = []          # model echoed the list, not selecting
    nt, seen = [], set()
    for t in obj.get("new", []):
        t = str(t).strip()
        if t and 1 < len(t) <= 40 and t.lower() not in VLOW and t.lower() not in seen:
            seen.add(t.lower()); nt.append(t)
    return vt[:MAXTAGS], nt[:3]

# ---- engines ----
class Apple:
    def __init__(self):
        exe = f"{HERE}/afm" if os.path.exists(f"{HERE}/afm") else None
        cmd = [exe] if exe else ["swift", f"{HERE}/afm.swift"]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    def ask(self, prompt):
        self.p.stdin.write(prompt.replace("\n", "") + "\n"); self.p.stdin.flush()
        return self.p.stdout.readline()
class Claude:
    def __init__(self):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key: raise SystemExit("claude engine needs ANTHROPIC_API_KEY (or use --engine apple).")
    def ask(self, prompt):
        import urllib.request
        body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 300,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=TIMEOUT))["content"][0]["text"]
class OpenAI:
    def __init__(self):
        self.key = os.environ.get("OPENAI_API_KEY")
        if not self.key: raise SystemExit("openai engine needs OPENAI_API_KEY.")
        self.model = MODEL or "gpt-4o-mini"
    def ask(self, prompt):
        import urllib.request
        body = json.dumps({"model": self.model, "max_tokens": 300,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.key}", "content-type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=TIMEOUT))["choices"][0]["message"]["content"]
class Gemini:
    # personal fanfic library: don't let safety filters drop mature/dark stories (the tag list itself lists such terms)
    SAFE = [{"category": c, "threshold": "BLOCK_NONE"} for c in
            ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
             "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]
    def __init__(self):
        self.key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.key: raise SystemExit("gemini engine needs GEMINI_API_KEY (or GOOGLE_API_KEY).")
        self.model = MODEL or "gemini-2.5-flash"
    def ask(self, prompt):
        import urllib.request
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.key}"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "safetySettings": self.SAFE}).encode()
        req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=TIMEOUT))
        cands = r.get("candidates")
        if not cands: raise RuntimeError("blocked:" + str(r.get("promptFeedback", {}).get("blockReason")))
        parts = cands[0].get("content", {}).get("parts")
        if not parts: raise RuntimeError("nocontent:" + str(cands[0].get("finishReason")))
        return parts[0]["text"]

PROP = f"{HERE}/classify_proposal.csv"
RANK = f"{HERE}/classify_newtags_ranked.csv"

if APPLY:                                      # apply 'added_tags' + stamp #wrangled — standalone, no LLM calls
    from common import run_writer                                     # writes shell out to calibre-debug
    rcon = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = collections.defaultdict(list)
    for b, t in rcon.execute("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag"): cur[b].append(t)
    have_wrangled = bool(rcon.execute("SELECT 1 FROM custom_columns WHERE label='wrangled'").fetchone())
    chg = {}
    for r in csv.DictReader(open(PROP)):
        b = int(r["book_id"]); tags = [t for t in r.get("added_tags", "").split("; ") if t.strip()]
        if tags: chg[str(b)] = sorted(set(cur.get(b, [])) | set(tags))   # union with current tags
    ops = []
    if not have_wrangled:                                             # first run: create + backfill whole library as wrangled-now
        ops.append({"op": "create_column", "label": "wrangled", "name": "Wrangled", "datatype": "datetime", "is_multiple": False})
        ops.append({"op": "stamp_now", "field": "#wrangled", "books": None})
    ops.append({"op": "set_field", "field": "tags", "values": chg})
    ops.append({"op": "stamp_now", "field": "#wrangled", "books": [int(b) for b in chg]})
    run_writer(ops)
    raise SystemExit(f"applied tags to {len(chg)} books + stamped #wrangled ({os.path.basename(PROP)}).")

# ---- gather books (read-only) ----
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c = con.cursor()
tagn = {b: 0 for (b,) in c.execute("SELECT id FROM books")}
for (b,) in c.execute("SELECT book FROM books_tags_link"): tagn[b] = tagn.get(b, 0) + 1
desc = {b: t for b, t in c.execute("SELECT book, text FROM comments")}
def strip_html(s): return re.sub(r"<[^>]+>", " ", s or "").strip()
SINCE = argval("--since", "")                     # explicit override: (re)process books with #updated >= this date
INCREMENTAL = "--incremental" in sys.argv         # per-book: (re)process books whose #updated > their #wrangled marker
updated = {}; wrangled = {}
if SINCE or INCREMENTAL:
    def _colmap(label):
        r = c.execute("SELECT id FROM custom_columns WHERE label=?", (label,)).fetchone()
        if not r: return {}
        i = r[0]; lk = f"books_custom_column_{i}_link"
        has = c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (lk,)).fetchone()[0]
        q = (f"SELECT l.book, v.value FROM {lk} l JOIN custom_column_{i} v ON v.id=l.value" if has
             else f"SELECT book, value FROM custom_column_{i}")
        return {b: str(v)[:10] for b, v in c.execute(q)}
    updated = _colmap("updated")
    if INCREMENTAL: wrangled = _colmap("wrangled")
def needs(b):                                     # changed since we last tagged it, or after an explicit --since date
    if INCREMENTAL and (b not in wrangled or updated.get(b, "") > wrangled.get(b, "")): return True
    if SINCE and updated.get(b, "") >= SINCE: return True
    return False
TEXTFALLBACK = "--text-fallback" in sys.argv     # when the description is thin, sample the book's own text
bookfile = {}
if TEXTFALLBACK:
    bp = {b: p for b, p in c.execute("SELECT id, path FROM books")}
    byb = {}
    for b, fmt, name in c.execute("SELECT book, format, name FROM data"):
        byb.setdefault(b, {})[fmt.upper()] = os.path.join(LIB, bp[b], name + "." + fmt.lower())
    for b, fm in byb.items():
        bookfile[b] = fm.get("EPUB") or next(iter(fm.values()))   # prefer EPUB, else any available format
def book_text(path, limit=6000):
    if not path or not os.path.exists(path): return ""
    if path.lower().endswith(".epub"):                  # fast path: epub is a zip of XHTML
        import zipfile
        try:
            z = zipfile.ZipFile(path); out = []
            for n in z.namelist():
                if not n.lower().endswith((".xhtml", ".html", ".htm")): continue
                t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "ignore"))).strip()
                if len(t) > 200: out.append(t)           # skip nav/title pages
                if sum(len(x) for x in out) > limit: break
            return " ".join(out)[:limit]
        except Exception: return ""
    import subprocess, tempfile                          # other formats (MOBI/PDF/DOCX/…): let calibre extract
    try:
        with tempfile.TemporaryDirectory() as td:
            o = os.path.join(td, "o.txt")
            subprocess.run(["ebook-convert", path, o], capture_output=True, timeout=180)
            return re.sub(r"\s+", " ", open(o, errors="ignore").read()).strip()[:limit] if os.path.exists(o) else ""
    except Exception: return ""
def text_for(b):
    d = strip_html(desc.get(b, ""))
    if len(d) >= 80 or not TEXTFALLBACK: return d
    et = book_text(bookfile.get(b, ""))
    return (d + " " + et).strip() if et else d
targets = [(b, text_for(b)) for b in tagn if tagn[b] < MIN_TAGS or needs(b)]
if INCREMENTAL or SINCE: print(f"  incremental: {sum(1 for b in tagn if needs(b))} books changed since last wrangle")
targets = [(b, t) for b, t in targets if t and len(t) >= 40]
titles = {b: t for b, t in c.execute("SELECT id, title FROM books")}
if LIMIT: targets = targets[:LIMIT]
print(f"engine={ENGINE}  books to process (< {MIN_TAGS} tags, has description): {len(targets)}")

import time
eng = {"apple": Apple, "claude": Claude, "openai": OpenAI, "gemini": Gemini}[ENGINE]()
def ask_retry(prompt, tries=4):
    err = ""
    for k in range(tries):
        try: return eng.ask(prompt), ""
        except RuntimeError as e:              # deterministic content block (no candidates) — retrying is futile
            return "", str(e)[:140]
        except Exception as e:                 # transient (HTTP 429/503, network) — back off and retry
            err = f"{type(e).__name__}: {e}"[:140]
            if k == tries - 1: return "", err
            time.sleep(2 ** k)

proposal, done = {}, set()                     # book -> (vocab_tags, proposed_new_tags)
if os.path.exists(PROP) and "--fresh" not in sys.argv:        # resume: skip books already in proposal
    for r in csv.DictReader(open(PROP)):
        bid = int(r["book_id"]); at = [t for t in r.get("added_tags", "").split("; ") if t.strip()]
        proposal[bid] = (at, [t for t in r.get("proposed_new", "").split("; ") if t.strip()])
        if (at or not TEXTFALLBACK) and not needs(bid): done.add(bid)   # re-process books changed since last wrangle
    if done: print(f"  resuming: {len(done)} already in proposal (pass --fresh to restart)")
def dump():
    with open(PROP, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["book_id", "title", "added_tags", "proposed_new"])
        for b, (vt, nt) in proposal.items(): w.writerow([b, titles.get(b, ""), "; ".join(vt), "; ".join(nt)])

from concurrent.futures import ThreadPoolExecutor, as_completed
todo = [(b, d) for b, d in targets if b not in done]
if BATCH: todo = todo[:BATCH]
def work(b, d):
    out, err = ask_retry(prompt_for(d)); vt, nt = parse_resp(out); return b, err, vt, nt
fails = nproc = 0; failures = []
print(f"  {len(todo)} to do this run, {WORKERS} concurrent")
prog = (Progress(TextColumn("[cyan]classifying"), BarColumn(), MofNCompleteColumn(),
                 TextColumn("· {task.fields[stat]}"), TimeRemainingColumn(), console=_con)
        if RICH and todo else None)
with ThreadPoolExecutor(max_workers=WORKERS) as ex, (prog or nullcontext()):
    task = prog.add_task("", total=len(todo), stat="") if prog else None
    futs = [ex.submit(work, b, d) for b, d in todo]
    for fut in as_completed(futs):
        b, err, vt, nt = fut.result()
        if err: fails += 1; failures.append((b, err))
        if vt or nt: proposal[b] = (vt, nt)
        nproc += 1
        if nproc % 50 == 0: dump()                # checkpoint regardless of UI
        stat = f"{sum(1 for v in proposal.values() if v[0])} tagged, {fails} failed"
        if prog: prog.update(task, advance=1, stat=stat)
        elif nproc % 50 == 0: print(f"  +{nproc}/{len(todo)} this run, {len(done)+nproc}/{len(targets)} total … {stat}")
dump()
if failures:
    with open(f"{HERE}/classify_failures.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["book_id", "title", "reason"])
        for b, e in failures: w.writerow([b, titles.get(b, ""), e])
    bytype = collections.Counter(e.split(":")[0].split(" ")[0] for _, e in failures)
    print(f"failures: {len(failures)} -> classify_failures.csv  by type: {dict(bytype)}")
    print("  (recover blocked books with a no-policy engine: python3 classify.py --engine apple)")

ranked = collections.Counter()
for vt, nt in proposal.values():
    for t in nt: ranked[t] += 1
with open(RANK, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["proposed_tag", "count"])
    for t, cnt in ranked.most_common(): w.writerow([t, cnt])
print(f"\nOutput 1 (apply): {sum(1 for v in proposal.values() if v[0])} books with vocab tags -> {os.path.basename(PROP)} (col 'added_tags')")
print(f"Output 2 (grow):  {len(ranked)} distinct new-tag candidates -> {os.path.basename(RANK)} (review -> promote into defaults/classify_vocab.txt)")
if RICH and ranked:
    tbl = Table(title="top new-tag candidates (review → promote into the vocab)")
    tbl.add_column("count", justify="right", style="cyan"); tbl.add_column("proposed tag")
    for tag, cnt in ranked.most_common(25): tbl.add_row(str(cnt), tag)
    _con.print(tbl)
else:
    print("top new-tag candidates:")
    for t, cnt in ranked.most_common(25): print(f"  {cnt:4}  {t}")
print("\nApply vocab tags with: python3 classify.py --apply   (Calibre closed)")
