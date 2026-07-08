#!/usr/bin/env python3
"""Content-based tagging from a controlled vocabulary, with TWO outputs per book:
  1) added_tags    — tags chosen from defaults/classify_vocab.txt (the consolidated set); these get APPLIED.
  2) proposed_new  — short reusable tags the model thinks apply but are NOT in the vocab yet; aggregated into
                     classify_newtags_ranked.csv for review, so the vocabulary grows cleanly (promote -> vocab).

  scourgify classify [--engine apple|claude|openai|gemini] [--incremental] [--workers N] [--batch N] [--fresh]
  scourgify classify --apply                    # apply 'added_tags' + stamp #wrangled (Calibre CLOSED; writes shell to calibre-debug)

Engines (--engine):  apple = on-device Apple Foundation Models via ./afm (free; macOS 26+).
          claude = Anthropic (ANTHROPIC_API_KEY) | openai = OpenAI (OPENAI_API_KEY) | gemini = Google (GEMINI_API_KEY) | mistral = Mistral (MISTRAL_API_KEY).
          --model overrides the per-engine default. Only books with < --min-tags tags AND a description are processed.
          Runs are resumable (skip books already in the proposal; --fresh restarts). Dry-run until --apply.
--incremental = cheap maintenance after new downloads: (re)process ONLY new/changed books — never classified,
          #updated newer than their own #wrangled marker, or re-fetched (added-date newer). --last N / --since DATE
          instead select by added/updated date ("update the last 30 books"). Scoped runs select exactly their books;
          the sparse-book default (< --min-tags) applies only when no scope flag is given. --apply auto-creates the
          #wrangled datetime column and stamps EVERY processed book, so the state lives IN the library — no external
          file. Selection semantics live in select.py (shared with the wizard header)."""
import argparse, os, re, csv, json, subprocess, collections, time, difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from scourgify import select
from scourgify.common import HERE, DATA, ro_connect, custom_column_id, run_writer, library
try:                                              # rich is optional: live dashboard/tables in system python3
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import Progress, BarColumn, TextColumn, MofNCompleteColumn, TimeRemainingColumn
    from rich.table import Table
    from rich.text import Text
    _con = Console(stderr=True); RICH = True
except ImportError:
    RICH = False

PROP = f"{DATA}/classify_proposal.csv"
RANK = f"{DATA}/classify_newtags_ranked.csv"
FAIL = f"{DATA}/classify_failures.csv"
AO3_VOCAB = f"{DATA}/ao3_vocab.csv"     # per-library canonical AO3 freeforms (name,uses); absent on fresh installs
SPEND_GATE = 200        # cloud runs above this many books require an explicit yes
DEDUP_CUTOFF = 0.86     # difflib ratio at/above which a proposed tag counts as a variant of an existing one

# $/MTok (input, output) for each engine's default model — public list prices as of 2026-07; edit when they change.
PRICING = {"apple": (0.0, 0.0), "claude": (1.00, 5.00), "openai": (0.15, 0.60), "gemini": (0.30, 2.50), "mistral": (0.20, 0.60)}

_VOCAB = None
def load_vocab():
    """Bundled vocab + optional CWD overrides/classify_vocab.txt (a line appends a term; '-term' removes one).
    Lazy so a packaging problem gives a real error at use, not at import, and installed users can override."""
    global _VOCAB
    if _VOCAB is None:
        terms = [l.strip() for l in open(f"{HERE}/defaults/classify_vocab.txt") if l.strip() and not l.startswith("#")]
        ov = os.path.join(os.getcwd(), "overrides", "classify_vocab.txt")
        if os.path.exists(ov):
            for l in open(ov):
                l = l.strip()
                if not l or l.startswith("#"): continue
                if l.startswith("-"): terms = [t for t in terms if t.lower() != l[1:].strip().lower()]
                elif l.lower() not in {t.lower() for t in terms}: terms.append(l)
        _VOCAB = terms
    return _VOCAB

_ALIASES = None
def load_aliases():
    """candidate -> target snaps from overrides/promote_aliases.csv (written by `scourgify promote --apply`),
    so tags we've decided are synonyms stop getting re-proposed as 'new'. {} if absent."""
    global _ALIASES
    if _ALIASES is None:
        p = os.path.join(os.getcwd(), "overrides", "promote_aliases.csv")
        _ALIASES = {}
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                if r.get("candidate") and r.get("target"):
                    _ALIASES[r["candidate"].strip().lower()] = r["target"].strip()
    return _ALIASES

_AO3 = None
def load_ao3_vocab():
    """The per-library AO3 canonical freeforms (data/ao3_vocab.csv 'name' column, built by ao3_import.py).
    Absent on a fresh install — degrade to [] silently; it's an extra reference layer, not a requirement."""
    global _AO3
    if _AO3 is None:
        try:
            _AO3 = [r["name"] for r in csv.DictReader(open(AO3_VOCAB)) if r.get("name", "").strip()]
        except OSError:
            _AO3 = []
    return _AO3

def existing_terms():
    """The reference a proposed-new tag is checked against: curated vocab ∪ ao3_vocab.csv, deduped
    case-insensitively with the curated spelling winning on collision (~1,450 terms — trivial for difflib)."""
    seen, out = set(), []
    for t in load_vocab() + load_ao3_vocab():
        if t.lower() not in seen:
            seen.add(t.lower()); out.append(t)
    return out

def est_cost(n_books, engine):
    """Rough list-price $ estimate for a run: input ≈ prompt chars/4 tokens, output ≈ 80 tokens/book."""
    i, o = PRICING.get(engine, (0.0, 0.0))
    tokens_in = (len(", ".join(load_vocab())) + 1900) / 4      # vocab + 1500-char description + instructions
    return n_books * (tokens_in * i + 80 * o) / 1e6


def prompt_for(desc, maxtags):
    return ("You are tagging a fanfiction story. Return ONLY a JSON object with two arrays:\n"
            f'  "tags": tags from the CONTROLLED LIST below that clearly apply (exact spelling, at most {maxtags}; '
            "be conservative; [] if vague; do NOT echo the whole list).\n"
            '  "new": up to 3 SHORT reusable trope/genre/theme tags (Title Case) that clearly apply but are NOT in the '
            "list and would be worth adding to the vocabulary. No plot specifics, character names, or fandoms; [] if none.\n"
            f"CONTROLLED LIST: {', '.join(load_vocab())}\n\nDESCRIPTION:\n{desc[:1500]}\n\nJSON:")

def parse_resp(text, maxtags=6, cutoff=DEDUP_CUTOFF):
    m = re.search(r"\{.*\}", text, re.S)
    if not m: return [], []
    try: obj = json.loads(m.group(0))
    except Exception: return [], []
    vlow = {v.lower(): v for v in load_vocab()}
    vkeys = list(vlow)                          # lowercased vocab, for the fuzzy near-miss snap below
    vt = [vlow[str(t).strip().lower()] for t in obj.get("tags", []) if str(t).strip().lower() in vlow]
    if len(vt) > maxtags * 2: vt = []          # model echoed the list, not selecting
    def keep(canon):
        if canon not in vt: vt.append(canon)   # snapped/exact hit -> apply the canonical vocab spelling
    nt, seen = [], set()
    for t in obj.get("new", []):
        t = str(t).strip(); tl = t.lower()
        # first char must be alphanumeric: also blocks =/+/-/@ spreadsheet-formula injection in the review CSVs
        if not (t and t[0].isalnum() and 1 < len(t) <= 40): continue
        if tl in vlow: keep(vlow[tl]); continue                     # already a vocab term the model mislabeled "new"
        al = load_aliases().get(tl)
        if al is not None:                          # a decided synonym: snap to vocab, else drop
            if al.lower() in vlow: keep(vlow[al.lower()])
            continue
        if tl in seen: continue
        near = difflib.get_close_matches(tl, vkeys, n=1, cutoff=cutoff)
        if near: keep(vlow[near[0]])           # ponytail: fuzzy snap can mis-map look-alikes; --dedup-cutoff tunes it
        else: seen.add(tl); nt.append(t)
    return vt[:maxtags], nt[:3]


def annotate_new(ranked, cutoff=DEDUP_CUTOFF, existing=None):
    """Smart review rows for the proposed-new tags: for each, its nearest existing tag
    (curated vocab ∪ ao3_vocab.csv) + similarity + verdict. Genuinely-new first (by count), near-dupes last.
    Pure (pass `existing` in tests) — this is the once-per-run matching against the full reference."""
    existing = existing_terms() if existing is None else existing
    elow = {e.lower(): e for e in existing}
    keys = list(elow)
    rows = []
    for tag, cnt in ranked.most_common():
        near = difflib.get_close_matches(tag.lower(), keys, n=1, cutoff=0.0)
        if near:
            nearest = elow[near[0]]
            sim = round(difflib.SequenceMatcher(None, tag.lower(), near[0]).ratio(), 2)
        else:
            nearest, sim = "", 0.0
        verdict = "near-duplicate" if sim >= cutoff else "new"
        rows.append([tag, cnt, nearest, sim, verdict])
    rows.sort(key=lambda r: (r[4] != "new", -r[1]))    # new first, then by descending count
    return rows


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

class Mistral:
    def __init__(self, model, timeout):
        self.key = os.environ.get("MISTRAL_API_KEY")
        if not self.key: raise SystemExit("mistral engine needs MISTRAL_API_KEY.")
        self.model = model or "mistral-small-latest"; self.timeout = timeout
    def ask(self, prompt):
        import urllib.request
        body = json.dumps({"model": self.model, "max_tokens": 300,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.mistral.ai/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.key}", "content-type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=self.timeout))["choices"][0]["message"]["content"]

ENGINES = {"apple": Apple, "claude": Claude, "openai": OpenAI, "gemini": Gemini, "mistral": Mistral}


# ---- live run display ----
def sparkline(vals, width=28):
    """Unicode sparkline of a numeric series (last `width` points), scaled to its max."""
    vals = [v for v in vals][-width:]
    if not vals: return ""
    blocks = "▁▂▃▄▅▆▇█"
    hi = max(vals)
    if hi <= 0: return blocks[0] * len(vals)
    return "".join(blocks[min(7, int(v * 8 / hi))] for v in vals)

class _Dashboard:
    """Live display for a classify run: progress bar, running numbers (tagged / failed /
    no-match / rate), a throughput sparkline, and the rising new-tag candidates.
    rich renders it live; without rich it degrades to a checkpoint line every 25 books."""
    BUCKET = 5.0                                   # seconds per throughput bucket

    def __init__(self, todo_n, done_before, targets_n):
        self.total, self.done_before, self.targets = todo_n, done_before, targets_n
        self.n = self.tagged = self.fails = 0
        self.newtags = collections.Counter()
        self.t0 = time.monotonic(); self.hist = [0]
        self.live = self.prog = self.task = None

    def __enter__(self):
        if RICH and self.total:
            self.prog = Progress(TextColumn("[cyan]classifying"), BarColumn(bar_width=None),
                                 MofNCompleteColumn(), TimeRemainingColumn(), console=_con)
            self.task = self.prog.add_task("", total=self.total)
            self.live = Live(self._render(), console=_con, refresh_per_second=4)
            self.live.__enter__()
        return self

    def __exit__(self, *exc):
        if self.live: self.live.__exit__(*exc)
        return False

    def update(self, vt, nt, err):
        self.n += 1
        if err: self.fails += 1
        elif vt: self.tagged += 1
        self.newtags.update(nt)
        b = int((time.monotonic() - self.t0) // self.BUCKET)
        while len(self.hist) <= b: self.hist.append(0)
        self.hist[b] += 1
        if self.live:
            self.prog.update(self.task, advance=1)
            self.live.update(self._render())
        elif self.n % 25 == 0:
            el = time.monotonic() - self.t0
            print(f"  +{self.n}/{self.total} … {self.tagged} tagged, {self.fails} failed, {self.n / el:.1f}/s")

    def _render(self):
        el = time.monotonic() - self.t0
        rate = self.n / el if el > 1 else 0.0
        g = Table.grid(padding=(0, 2))
        g.add_row("[bold]this run[/]", f"{self.n}/{self.total}",
                  "[green]tagged[/]", str(self.tagged),
                  "[red]failed[/]", str(self.fails),
                  "[dim]no match[/]", str(max(0, self.n - self.tagged - self.fails)),
                  "[bold]rate[/]", f"{rate:.1f}/s")
        parts = [self.prog, g]
        spark = sparkline(self.hist)
        if spark: parts.append(Text.assemble(("throughput  ", "bold"), (spark, "cyan")))
        if self.newtags:
            top = " · ".join(f"{t} ×{c}" for t, c in self.newtags.most_common(5))
            parts.append(Text.assemble(("rising candidates  ", "bold"), (top, "magenta")))
        return Panel(Group(*parts), border_style="cyan", padding=(0, 1),
                     title=f"classify — {self.done_before + self.n}/{self.targets} total")


# ---- apply: 'added_tags' + stamp #wrangled — standalone, no LLM calls ----
def apply_proposal():
    if not os.path.exists(PROP):
        raise SystemExit(f"no proposal to apply ({os.path.basename(PROP)} not found — run a classify pass first).")
    con = ro_connect()
    cur = collections.defaultdict(list)
    for b, t in con.execute("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag"): cur[b].append(t)
    have_wrangled = custom_column_id(con, "wrangled") is not None
    chg, processed = {}, []
    for r in csv.DictReader(open(PROP)):
        b = int(r["book_id"]); processed.append(b)
        tags = [t for t in r.get("added_tags", "").split("; ") if t.strip()]
        if tags: chg[str(b)] = sorted(set(cur.get(b, [])) | set(tags))   # union with current tags
    ops = []
    if not have_wrangled:                                             # first run: create + backfill whole library as wrangled-now
        ops.append({"op": "create_column", "label": "wrangled", "name": "Wrangled", "datatype": "datetime", "is_multiple": False})
        ops.append({"op": "stamp_now", "field": "#wrangled", "books": None})
    ops.append({"op": "set_field", "field": "tags", "values": chg})
    # stamp EVERY processed book, tagged or not — an unstamped no-tag book would be re-sent to the LLM forever
    ops.append({"op": "stamp_now", "field": "#wrangled", "books": processed})
    run_writer(ops)
    # archive so a later --apply can't re-add tags you've since hand-removed (stale rows never re-apply)
    arch = PROP.replace(".csv", f"_applied_{time.strftime('%Y%m%d-%H%M%S')}.csv")
    os.rename(PROP, arch)
    print(f"applied tags to {len(chg)} books + stamped #wrangled on {len(processed)} processed; proposal archived -> {os.path.basename(arch)}")


PROP_COLS = ["book_id", "title", "added_tags", "proposed_new"]
def _write_prop(rows):
    with open(PROP, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PROP_COLS, extrasaction="ignore"); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in PROP_COLS})


def apply_proposal_step():
    """1-by-1 review of the proposal: each book's proposed tags as a checklist. Accepted tags are
    applied + the book stamped; rejected tags are dropped and logged (class=ai, a hallucination filter,
    NOT a rule bug). Skip/quit leave a book's row pending in the proposal for a later run."""
    if not os.path.exists(PROP):
        raise SystemExit(f"no proposal to apply ({os.path.basename(PROP)} not found — run a classify pass first).")
    from scourgify import ui
    if not ui.interactive():
        raise SystemExit("--step needs an interactive terminal (omit it to apply the whole proposal).")
    from scourgify.common import log_rejects
    con = ro_connect()
    desc = {b: strip_html(t) for b, t in con.execute("SELECT book, text FROM comments")}
    titles = {b: t for b, t in con.execute("SELECT id, title FROM books")}
    decided, pending, rejects, quit_ = [], [], [], False
    for r in csv.DictReader(open(PROP)):
        tags = [t for t in r.get("added_tags", "").split("; ") if t.strip()]
        if quit_: pending.append(r); continue
        if not tags: decided.append(r); continue               # no-tag book: stamp only (else re-sent forever)
        b = int(r["book_id"]); title = str(r.get("title") or titles.get(b, ""))
        acc, rej, action = ui.checklist(f"[bold]#{b}[/]  {title[:64]}", tags, subtitle=(desc.get(b, "")[:280] or "(no description)"))
        if action == "quit": quit_ = True; pending.append(r); continue
        if action == "skip": pending.append(r); continue
        for i in rej:
            rejects.append({"stage": "classify", "book": b, "title": title, "kind": "add",
                            "column": "tags", "before": "", "after": tags[i], "class": "ai"})
        decided.append({**r, "added_tags": "; ".join(tags[i] for i in acc)})
    log_rejects(rejects)
    if not decided:
        print("(nothing decided — proposal left untouched.)"); return
    _write_prop(decided); apply_proposal()                     # applies + stamps the decided rows, archives PROP
    if pending:
        _write_prop(pending)
        print(f"{len(pending)} book(s) left pending for a later run -> {os.path.basename(PROP)}")


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
    """-> (targets [(book, text)], titles, needs). Scope comes from the flags, first match wins:
    --incremental / --last N / --since DATE select ONLY matching books (newest-added-first);
    bare classify keeps the sparse mode (fewer than --min-tags tags). `needs(b)` is True for
    explicitly scoped books — the resume logic uses it to re-process them even if already proposed."""
    con = ro_connect(); c = con.cursor()
    if a.incremental: ids, scope = select.pick(con, "incremental"), "new/changed since last classify"
    elif a.last:      ids, scope = select.pick(con, "last", n=a.last), f"last {a.last} added"
    elif a.since:     ids, scope = select.pick(con, "since", since=a.since), f"added/updated since {a.since}"
    else:             ids, scope = select.pick(con, "sparse", min_tags=a.min_tags), f"fewer than {a.min_tags} tags"
    explicit = set(ids) if (a.incremental or a.last or a.since) else set()
    def needs(b): return b in explicit
    desc = {b: t for b, t in c.execute("SELECT book, text FROM comments")}
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
    targets = [(b, text_for(b)) for b in ids]
    kept = [(b, t) for b, t in targets if t and len(t) >= 40]
    print(f"  scope: {scope} -> {len(ids)} books")
    if len(kept) < len(targets):                  # no silent drops: thin descriptions are reported, not vanished
        print(f"  note: {len(targets) - len(kept)} dropped (description under 40 chars"
              + (")" if a.text_fallback else "; --text-fallback samples the book text instead)"))
    titles = {b: t for b, t in c.execute("SELECT id, title FROM books")}
    if a.limit: kept = kept[:a.limit]
    return kept, titles, needs


def bakeoff(a, targets, engines, n=5):
    """The same n sample books through each engine, sequentially — for comparing output quality
    before committing to a full run. -> {book: {engine: (vocab_tags, new_tags, err)}}.
    Display-only: never touches the proposal CSV."""
    out = {}
    for e in engines:
        eng = ENGINES[e]("", a.timeout)                       # per-engine default model
        for b, d in targets[:n]:
            try:
                vt, nt = parse_resp(eng.ask(prompt_for(d, a.max_tags)), a.max_tags, a.dedup_cutoff); err = ""
            except Exception as ex:
                vt, nt, err = [], [], f"{type(ex).__name__}: {ex}"[:60]
            out.setdefault(b, {})[e] = (vt, nt, err)
    return out


def ask_retry(eng, prompt, tries=4):
    """Call eng.ask(prompt) with backoff. -> (text, "") on success; ("", reason) on failure.
    RuntimeError = deterministic content block (no retry); other errors retry with 2**k backoff."""
    err = ""
    for k in range(tries):
        try: return eng.ask(prompt), ""
        except RuntimeError as e:
            return "", str(e)[:140]
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:140]
            if k == tries - 1: return "", err
            time.sleep(2 ** k)
    return "", err


def classify_run(a):
    targets, titles, needs = gather(a)
    print(f"engine={a.engine}  candidate books: {len(targets)}")

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
    def work(b, d):
        out, err = ask_retry(eng, prompt_for(d, a.max_tags)); vt, nt = parse_resp(out, a.max_tags, a.dedup_cutoff); return b, err, vt, nt

    failures = []
    print(f"  {len(todo)} to do this run, {a.workers} concurrent")
    ex = ThreadPoolExecutor(max_workers=a.workers)
    interrupted = False
    try:
        with _Dashboard(len(todo), len(done), len(targets)) as dash:
            futs = [ex.submit(work, b, d) for b, d in todo]
            for fut in as_completed(futs):
                b, err, vt, nt = fut.result()
                if err: failures.append((b, err))
                if vt or nt: proposal[b] = (vt, nt)
                dash.update(vt, nt, err)
                if dash.n % 50 == 0: dump()           # checkpoint regardless of UI
    except KeyboardInterrupt:
        # Ctrl+C: never start queued work, don't wait for in-flight requests (they're
        # abandoned; runs are resumable so nothing is lost beyond the requests in the air)
        interrupted = True
        ex.shutdown(wait=False, cancel_futures=True)
    else:
        ex.shutdown()
    dump()
    if interrupted:
        print(f"\n  interrupted — {len(proposal)} results saved to the proposal; re-run to resume where you left off.")
    if failures:
        with open(FAIL, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["book_id", "title", "reason"])
            for b, e in failures: w.writerow([b, titles.get(b, ""), e])
        bytype = collections.Counter(e.split(":")[0].split(" ")[0] for _, e in failures)
        print(f"failures: {len(failures)} -> {os.path.basename(FAIL)}  by type: {dict(bytype)}")
        print("  (recover blocked books with a no-policy engine: scourgify classify --engine apple)")

    ranked = collections.Counter()
    for vt, nt in proposal.values():
        for t in nt: ranked[t] += 1
    rows = annotate_new(ranked, a.dedup_cutoff)               # nearest existing tag + verdict for each candidate
    fresh = [r for r in rows if r[4] == "new"]                # genuinely novel — the ones worth promoting
    with open(RANK, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["proposed_tag", "count", "nearest_existing", "similarity", "verdict"])
        w.writerows(rows)
    print(f"\nOutput 1 (apply): {sum(1 for v in proposal.values() if v[0])} books with vocab tags -> {os.path.basename(PROP)} (col 'added_tags')")
    print(f"Output 2 (grow):  {len(fresh)} new + {len(rows) - len(fresh)} near-dupes of existing tags -> {os.path.basename(RANK)} (promote 'verdict=new' rows into defaults/classify_vocab.txt)")
    if RICH and rows:
        tbl = Table(title="top new-tag candidates (verdict=new → promote; near-duplicate ≈ an existing tag)")
        tbl.add_column("count", justify="right", style="cyan"); tbl.add_column("proposed tag")
        tbl.add_column("nearest existing", style="dim"); tbl.add_column("verdict")
        for tag, cnt, nearest, sim, verdict in rows[:25]:
            tbl.add_row(str(cnt), tag, f"{nearest} ({sim})" if nearest else "",
                        f"[green]new[/]" if verdict == "new" else f"[yellow]≈ dupe[/]")
        _con.print(tbl)
    elif rows:
        print("top new-tag candidates (verdict | count | tag | nearest existing):")
        for tag, cnt, nearest, sim, verdict in rows[:25]:
            print(f"  {verdict:14} {cnt:4}  {tag}" + (f"  ≈ {nearest} ({sim})" if nearest else ""))
    print("\nApply vocab tags with: scourgify classify --apply   (Calibre closed)")


def build_parser():
    p = argparse.ArgumentParser(description="Content-based tagging from a controlled vocabulary (LLM engines; dry-run until --apply).")
    p.add_argument("--engine", default="apple", choices=sorted(ENGINES), help="apple = on-device, free (default)")
    p.add_argument("--apply", action="store_true", help="apply 'added_tags' from the proposal + stamp #wrangled (Calibre closed)")
    p.add_argument("--step", action="store_true", help="with --apply: review each book's tags 1-by-1 (interactive; untick to reject)")
    p.add_argument("--incremental", action="store_true", help="only new/changed books (never classified, #updated newer than their #wrangled marker, or re-fetched)")
    p.add_argument("--last", type=int, default=0, metavar="N", help="(re)process the N most recently added books")
    p.add_argument("--since", default="", metavar="DATE", help="(re)process books added or site-updated on/after this ISO date")
    p.add_argument("--fresh", action="store_true", help="ignore the existing proposal and restart (a full cloud pass costs real money)")
    p.add_argument("--batch", type=int, default=0, metavar="N", help="process only N new books this run (re-run resumes)")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="hard cap on candidate books")
    p.add_argument("--workers", type=int, default=8, metavar="N", help="concurrent API requests (cloud engines are I/O-bound)")
    p.add_argument("--min-tags", type=int, default=2, metavar="N", help="process books with fewer than N tags")
    p.add_argument("--max-tags", type=int, default=6, metavar="N", help="max vocab tags per book")
    p.add_argument("--dedup-cutoff", type=float, default=DEDUP_CUTOFF, metavar="R",
                   help=f"difflib ratio (0-1) to treat a proposed tag as a variant of an existing one (default {DEDUP_CUTOFF})")
    p.add_argument("--model", default="", help="override the per-engine default model")
    p.add_argument("--timeout", type=int, default=60, metavar="S", help="per-request HTTP timeout")
    p.add_argument("--text-fallback", action="store_true", help="sample the book's own prose when the description is thin")
    p.add_argument("--yes", "-y", action="store_true", help="skip the large-cloud-run confirmation")
    return p

def normalize(a):
    """Post-parse invariants shared by the CLI and the wizard."""
    if a.engine == "apple": a.workers = 1        # apple = one subprocess pipe, not thread-safe
    library()                                    # fail fast with a clear message
    os.makedirs(DATA, exist_ok=True)
    return a

def main():
    a = normalize(build_parser().parse_args())
    if a.apply: apply_proposal_step() if a.step else apply_proposal()
    else: classify_run(a)


if __name__ == "__main__":
    main()
