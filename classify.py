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
import argparse, os, re, csv, json, subprocess, collections, time
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from common import HERE, ro_connect, read_custom_column, custom_column_id, run_writer, library
try:                                              # rich is optional: pretty progress/tables in system python3
    from rich.console import Console
    from rich.progress import Progress, BarColumn, TextColumn, MofNCompleteColumn, TimeRemainingColumn
    from rich.table import Table
    _con = Console(stderr=True); RICH = True
except ImportError:
    RICH = False

VOCAB = [l.strip() for l in open(f"{HERE}/defaults/classify_vocab.txt") if l.strip() and not l.startswith("#")]
VLOW = {v.lower(): v for v in VOCAB}
PROP = f"{HERE}/classify_proposal.csv"
RANK = f"{HERE}/classify_newtags_ranked.csv"
FAIL = f"{HERE}/classify_failures.csv"
SPEND_GATE = 200        # cloud runs above this many books require an explicit yes


def prompt_for(desc, maxtags):
    return ("You are tagging a fanfiction story. Return ONLY a JSON object with two arrays:\n"
            f'  "tags": tags from the CONTROLLED LIST below that clearly apply (exact spelling, at most {maxtags}; '
            "be conservative; [] if vague; do NOT echo the whole list).\n"
            '  "new": up to 3 SHORT reusable trope/genre/theme tags (Title Case) that clearly apply but are NOT in the '
            "list and would be worth adding to the vocabulary. No plot specifics, character names, or fandoms; [] if none.\n"
            f"CONTROLLED LIST: {', '.join(VOCAB)}\n\nDESCRIPTION:\n{desc[:1500]}\n\nJSON:")

def parse_resp(text, maxtags=6):
    m = re.search(r"\{.*\}", text, re.S)
    if not m: return [], []
    try: obj = json.loads(m.group(0))
    except Exception: return [], []
    vt = [VLOW[str(t).strip().lower()] for t in obj.get("tags", []) if str(t).strip().lower() in VLOW]
    if len(vt) > maxtags * 2: vt = []          # model echoed the list, not selecting
    nt, seen = [], set()
    for t in obj.get("new", []):
        t = str(t).strip()
        # first char must be alphanumeric: also blocks =/+/-/@ spreadsheet-formula injection in the review CSVs
        if t and t[0].isalnum() and 1 < len(t) <= 40 and t.lower() not in VLOW and t.lower() not in seen:
            seen.add(t.lower()); nt.append(t)
    return vt[:maxtags], nt[:3]


# ---- engines ----
class Apple:
    def __init__(self, model, timeout):
        exe = f"{HERE}/afm" if os.path.exists(f"{HERE}/afm") else None
        cmd = [exe] if exe else ["swift", f"{HERE}/afm.swift"]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    def ask(self, prompt):
        self.p.stdin.write(prompt.replace("\n", "") + "\n"); self.p.stdin.flush()
        return self.p.stdout.readline()

class Claude:
    def __init__(self, model, timeout):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key: raise SystemExit("claude engine needs ANTHROPIC_API_KEY (or use --engine apple).")
        self.model = model or "claude-haiku-4-5-20251001"; self.timeout = timeout
    def ask(self, prompt):
        import urllib.request
        body = json.dumps({"model": self.model, "max_tokens": 300,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=self.timeout))["content"][0]["text"]

class OpenAI:
    def __init__(self, model, timeout):
        self.key = os.environ.get("OPENAI_API_KEY")
        if not self.key: raise SystemExit("openai engine needs OPENAI_API_KEY.")
        self.model = model or "gpt-4o-mini"; self.timeout = timeout
    def ask(self, prompt):
        import urllib.request
        body = json.dumps({"model": self.model, "max_tokens": 300,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.key}", "content-type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=self.timeout))["choices"][0]["message"]["content"]

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
        import urllib.request
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "safetySettings": self.SAFE,
                           "generationConfig": {"maxOutputTokens": 2048}}).encode()   # bound cost; roomy for thinking models
        req = urllib.request.Request(url, data=body,
            headers={"content-type": "application/json", "x-goog-api-key": self.key})  # key in header, never in the URL
        r = json.load(urllib.request.urlopen(req, timeout=self.timeout))
        cands = r.get("candidates")
        if not cands: raise RuntimeError("blocked:" + str(r.get("promptFeedback", {}).get("blockReason")))
        parts = cands[0].get("content", {}).get("parts")
        if not parts: raise RuntimeError("nocontent:" + str(cands[0].get("finishReason")))
        return parts[0]["text"]

ENGINES = {"apple": Apple, "claude": Claude, "openai": OpenAI, "gemini": Gemini}


# ---- apply: 'added_tags' + stamp #wrangled — standalone, no LLM calls ----
def apply_proposal():
    if not os.path.exists(PROP):
        raise SystemExit(f"no proposal to apply ({os.path.basename(PROP)} not found — run a classify pass first).")
    con = ro_connect()
    cur = collections.defaultdict(list)
    for b, t in con.execute("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag"): cur[b].append(t)
    have_wrangled = custom_column_id(con, "wrangled") is not None
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
    # archive so a later --apply can't re-add tags you've since hand-removed (stale rows never re-apply)
    arch = PROP.replace(".csv", f"_applied_{time.strftime('%Y%m%d-%H%M%S')}.csv")
    os.rename(PROP, arch)
    print(f"applied tags to {len(chg)} books + stamped #wrangled; proposal archived -> {os.path.basename(arch)}")


# ---- gather books (read-only) ----
def strip_html(s): return re.sub(r"<[^>]+>", " ", s or "").strip()

def book_text(path, limit=6000):
    if not path or not os.path.exists(path): return ""
    if path.lower().endswith(".epub"):                  # fast path: epub is a zip of XHTML
        import zipfile
        try:
            z = zipfile.ZipFile(path); out = []
            for n in z.namelist():
                if not n.lower().endswith((".xhtml", ".html", ".htm")): continue
                if z.getinfo(n).file_size > 2_000_000: continue        # untrusted download: skip zip-bomb members
                t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "ignore"))).strip()
                if len(t) > 200: out.append(t)           # skip nav/title pages
                if sum(len(x) for x in out) > limit: break
            return " ".join(out)[:limit]
        except Exception: return ""
    import tempfile                                      # other formats (MOBI/PDF/DOCX/…): let calibre extract
    try:
        with tempfile.TemporaryDirectory() as td:
            o = os.path.join(td, "o.txt")
            subprocess.run(["ebook-convert", path, o], capture_output=True, timeout=180)
            return re.sub(r"\s+", " ", open(o, errors="ignore").read()).strip()[:limit] if os.path.exists(o) else ""
    except Exception: return ""

def gather(a):
    """-> (targets [(book, text)], titles, needs) for books under --min-tags or changed since last wrangle."""
    con = ro_connect(); c = con.cursor()
    tagn = {b: 0 for (b,) in c.execute("SELECT id FROM books")}
    for (b,) in c.execute("SELECT book FROM books_tags_link"): tagn[b] = tagn.get(b, 0) + 1
    desc = {b: t for b, t in c.execute("SELECT book, text FROM comments")}
    updated = wrangled = {}
    if a.since or a.incremental:
        updated = {b: str(v)[:10] for b, v in (read_custom_column(con, "#updated") or {}).items()}
        if a.incremental: wrangled = {b: str(v)[:10] for b, v in (read_custom_column(con, "#wrangled") or {}).items()}
    def needs(b):                                 # changed since we last tagged it, or after an explicit --since date
        if a.incremental and (b not in wrangled or updated.get(b, "") > wrangled.get(b, "")): return True
        if a.since and updated.get(b, "") >= a.since: return True
        return False
    bookfile = {}
    if a.text_fallback:                           # when the description is thin, sample the book's own text
        bp = {b: p for b, p in c.execute("SELECT id, path FROM books")}
        byb = {}
        for b, fmt, name in c.execute("SELECT book, format, name FROM data"):
            byb.setdefault(b, {})[fmt.upper()] = os.path.join(library(), bp[b], name + "." + fmt.lower())
        for b, fm in byb.items():
            bookfile[b] = fm.get("EPUB") or next(iter(fm.values()))   # prefer EPUB, else any available format
    def text_for(b):
        d = strip_html(desc.get(b, ""))
        if len(d) >= 80 or not a.text_fallback: return d
        et = book_text(bookfile.get(b, ""))
        return (d + " " + et).strip() if et else d
    targets = [(b, text_for(b)) for b in tagn if tagn[b] < a.min_tags or needs(b)]
    if a.incremental or a.since: print(f"  incremental: {sum(1 for b in tagn if needs(b))} books changed since last wrangle")
    targets = [(b, t) for b, t in targets if t and len(t) >= 40]
    titles = {b: t for b, t in c.execute("SELECT id, title FROM books")}
    if a.limit: targets = targets[:a.limit]
    return targets, titles, needs


def classify_run(a):
    targets, titles, needs = gather(a)
    print(f"engine={a.engine}  books to process (< {a.min_tags} tags, has description): {len(targets)}")

    proposal, done = {}, set()                     # book -> (vocab_tags, proposed_new_tags)
    if os.path.exists(PROP) and not a.fresh:       # resume: skip books already in proposal
        for r in csv.DictReader(open(PROP)):
            bid = int(r["book_id"]); at = [t for t in r.get("added_tags", "").split("; ") if t.strip()]
            proposal[bid] = (at, [t for t in r.get("proposed_new", "").split("; ") if t.strip()])
            if (at or not a.text_fallback) and not needs(bid): done.add(bid)   # re-process books changed since last wrangle
        if done: print(f"  resuming: {len(done)} already in proposal (pass --fresh to restart)")
    def dump():
        with open(PROP, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["book_id", "title", "added_tags", "proposed_new"])
            for b, (vt, nt) in proposal.items(): w.writerow([b, titles.get(b, ""), "; ".join(vt), "; ".join(nt)])

    todo = [(b, d) for b, d in targets if b not in done]
    if a.batch: todo = todo[:a.batch]
    if a.engine != "apple" and len(todo) > SPEND_GATE and not a.yes:   # spend gate: cloud runs cost real money
        import sys
        msg = f"about to send {len(todo)} books to the {a.engine} API (costs money; --incremental/--batch shrink it)."
        if sys.stdin.isatty() and input(f"  {msg} proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("aborted (nothing sent).")
        elif not sys.stdin.isatty():
            raise SystemExit(f"  {msg}\n  non-interactive: re-run with --yes to confirm.")

    eng = ENGINES[a.engine](a.model, a.timeout)
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
    def work(b, d):
        out, err = ask_retry(prompt_for(d, a.max_tags)); vt, nt = parse_resp(out, a.max_tags); return b, err, vt, nt

    fails = nproc = 0; failures = []
    print(f"  {len(todo)} to do this run, {a.workers} concurrent")
    prog = (Progress(TextColumn("[cyan]classifying"), BarColumn(), MofNCompleteColumn(),
                     TextColumn("· {task.fields[stat]}"), TimeRemainingColumn(), console=_con)
            if RICH and todo else None)
    with ThreadPoolExecutor(max_workers=a.workers) as ex, (prog or nullcontext()):
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
        with open(FAIL, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["book_id", "title", "reason"])
            for b, e in failures: w.writerow([b, titles.get(b, ""), e])
        bytype = collections.Counter(e.split(":")[0].split(" ")[0] for _, e in failures)
        print(f"failures: {len(failures)} -> {os.path.basename(FAIL)}  by type: {dict(bytype)}")
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


def main():
    p = argparse.ArgumentParser(description="Content-based tagging from a controlled vocabulary (LLM engines; dry-run until --apply).")
    p.add_argument("--engine", default="apple", choices=sorted(ENGINES), help="apple = on-device, free (default)")
    p.add_argument("--apply", action="store_true", help="apply 'added_tags' from the proposal + stamp #wrangled (Calibre closed)")
    p.add_argument("--incremental", action="store_true", help="only books whose #updated is newer than their #wrangled marker")
    p.add_argument("--since", default="", metavar="DATE", help="(re)process books with #updated >= this ISO date")
    p.add_argument("--fresh", action="store_true", help="ignore the existing proposal and restart (a full cloud pass costs real money)")
    p.add_argument("--batch", type=int, default=0, metavar="N", help="process only N new books this run (re-run resumes)")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="hard cap on candidate books")
    p.add_argument("--workers", type=int, default=8, metavar="N", help="concurrent API requests (cloud engines are I/O-bound)")
    p.add_argument("--min-tags", type=int, default=2, metavar="N", help="process books with fewer than N tags")
    p.add_argument("--max-tags", type=int, default=6, metavar="N", help="max vocab tags per book")
    p.add_argument("--model", default="", help="override the per-engine default model")
    p.add_argument("--timeout", type=int, default=60, metavar="S", help="per-request HTTP timeout")
    p.add_argument("--text-fallback", action="store_true", help="sample the book's own prose when the description is thin")
    p.add_argument("--yes", "-y", action="store_true", help="skip the large-cloud-run confirmation")
    a = p.parse_args()
    if a.engine == "apple": a.workers = 1        # apple = one subprocess pipe, not thread-safe
    library()                                    # fail fast with a clear message
    if a.apply: apply_proposal()
    else: classify_run(a)


if __name__ == "__main__":
    main()
