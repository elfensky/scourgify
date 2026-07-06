# Tag-Promotion (`scourgify promote`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `scourgify promote` command that adversarially reasons each novel tag candidate (from `classify_newtags_ranked.csv`) against the master tag list and decides promote / alias / reject, writing a reviewed artifact a human applies.

**Architecture:** One in-package subcommand using classify's existing pluggable LLM engines. Per candidate: an **advocate** prompt proposes a verdict, a **skeptic** prompt (optionally on a different engine via `--verify-with`) tries to refute a promote; the skeptic's refute wins and the row is flagged `contested` for human review (the human review is the referee — no third LLM call, per the `/octo:debate` outcome). Dry-run writes `data/promote_review.csv`; `promote --apply` folds decisions into `overrides/`. A decision ledger makes re-runs skip already-decided candidates; applied aliases feed `parse_resp` so the pool shrinks.

**Tech Stack:** Python 3.10+ stdlib only (`csv`, `json`, `re`, `difflib`, `urllib`, `concurrent.futures`); `rich` optional (try/except in core tools). No new dependencies.

## Global Constraints

- **Python floor:** `>=3.10` — no `match` guards or 3.11+ syntax that breaks 3.10.
- **Stdlib only** in `classify`/`promote`/`common`; `rich` only under `try/except` with a plain fallback.
- **Never import `rich`/`ui`/`wizard` from `_writer.py`** (irrelevant here, but the rule stands).
- **scourgify ships/brokers NO API keys** — every cloud engine reads the user's own env var; `apple` needs none.
- **Reads vs writes:** all reads are read-only sqlite; the only library writes go through `common.run_writer`. This feature writes only to CSV/text files under `data/` and `overrides/` — never the Calibre DB.
- **Tests:** plain-assert, pytest-compatible, no network/library/fixtures. Run with `uv run tests/<file>.py`. The `__main__` block auto-collects `test_*` from globals (copy the runner footer from `tests/test_core.py`).
- **Data files** live under `DATA` (`common.DATA` == `os.getcwd()/data`, gitignored) except user overrides under `overrides/` (gitignored).
- **Commit message trailer:** end every commit body with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Branch:** all work on `feat/tag-promotion` (already checked out).

---

## File Structure

- **Modify `src/scourgify/classify.py`:** factor `ask_retry` to module level (Task 1); add `Mistral` engine + `PRICING` row (Task 2); add `load_aliases()` + extend `parse_resp` with the applied-alias hard-map (Task 8).
- **Create `src/scourgify/promote.py`:** the whole feature — `parse_decision` (Task 3), `shortlist` + prompt builders (Task 4), `candidates` (Task 5), `decide` (Task 6), `apply_decisions` (Task 7), CLI `build_parser`/`normalize`/`main` + run loop (Task 9).
- **Modify `src/scourgify/cli.py`:** dispatch `promote` (Task 9).
- **Create `tests/test_promote.py`:** all pure-function tests (Tasks 1,3-8) + a fake-engine integration test (Task 9).
- **Modify `README.md`, `CLAUDE.md`:** document the promotion step, engines/keys, Mistral, `--verify-with` (Task 10).

Shared constants (define at the top of `promote.py`):
```python
from scourgify.classify import RANK, PROP, ENGINES, existing_terms, ask_retry
from scourgify.common import DATA, library
LEDGER = f"{DATA}/promote_ledger.csv"                       # decided candidates (skip-list): tag,verdict,target
REVIEW = f"{DATA}/promote_review.csv"                       # pending AI verdicts for human review
ALIASES = os.path.join(os.getcwd(), "overrides", "promote_aliases.csv")   # candidate,target for parse_resp snapping
```

Decision dict shape (consistent across all tasks):
```python
{"tag": str, "count": int, "verdict": "promote"|"alias"|"reject",
 "target": str,            # "" unless alias
 "reason": str, "confidence": "low"|"med"|"high",
 "contested": bool}        # advocate said promote, skeptic overrode
```

---

### Task 1: Factor `ask_retry` to a module-level helper in `classify.py`

**Files:**
- Modify: `src/scourgify/classify.py` (the nested closure at lines 407-416, and its call sites in `work` at 418 and `bakeoff` ~line 373)
- Test: `tests/test_promote.py`

**Interfaces:**
- Produces: `ask_retry(eng, prompt, tries=4) -> (out: str, err: str)` at module level. `eng` is any object with `.ask(prompt)->str`. `RuntimeError` from `eng.ask` → immediate `("", msg)` (deterministic block); other exceptions → retry with `2**k` backoff, returning `("", msg)` after the last try.

- [ ] **Step 1: Write the failing test** (append to `tests/test_promote.py`; create the file with the header + runner footer from `tests/test_core.py` first)

```python
def test_ask_retry_success_and_block():
    from scourgify.classify import ask_retry
    class OK:
        def ask(self, p): return "yes"
    assert ask_retry(OK(), "x") == ("yes", "")

    class Blocked:
        def ask(self, p): raise RuntimeError("blocked:PROHIBITED")
    out, err = ask_retry(Blocked(), "x")
    assert out == "" and err.startswith("blocked:")

    class Flaky:                                 # fails once, then succeeds — but tries=1 gives up immediately
        def __init__(self): self.n = 0
        def ask(self, p):
            self.n += 1
            if self.n == 1: raise ValueError("429")
            return "ok"
    out, err = ask_retry(Flaky(), "x", tries=1)
    assert out == "" and "ValueError" in err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run tests/test_promote.py`
Expected: FAIL — `ImportError: cannot import name 'ask_retry'`

- [ ] **Step 3: Add the module-level function** in `classify.py`, immediately above `def classify_run(a):` (line 380):

```python
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
```

- [ ] **Step 4: Replace the nested closure.** In `classify_run` delete the `def ask_retry(prompt, tries=4):` block (lines 407-416) and change `work` (line 418) from `ask_retry(prompt_for(...))` to `ask_retry(eng, prompt_for(d, a.max_tags))`. In `bakeoff` (~line 373) it already calls `eng.ask(...)` directly inside a try — leave it. Grep to confirm no other caller of the old closure remains:

Run: `grep -n 'ask_retry' src/scourgify/classify.py`
Expected: the new `def ask_retry(eng, prompt` and the call `ask_retry(eng, prompt_for(...))` — no `def ask_retry(prompt` closure.

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run tests/test_promote.py && uv run tests/test_core.py`
Expected: both PASS (classify's existing behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/scourgify/classify.py tests/test_promote.py
git commit -m "refactor(classify): factor ask_retry to a reusable module-level helper

$(printf 'Shared by classify_run and the upcoming promote command.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Add the `Mistral` engine + `PRICING` row

**Files:**
- Modify: `src/scourgify/classify.py` (after the `Gemini` class, ~line 200, and the `ENGINES`/`PRICING` lines 202/43)
- Test: `tests/test_promote.py`

**Interfaces:**
- Produces: `ENGINES["mistral"] = Mistral`; `PRICING["mistral"] = (0.20, 0.60)`. `Mistral(model, timeout)` reads `MISTRAL_API_KEY`, `SystemExit` if missing; `.ask(prompt)` POSTs to `https://api.mistral.ai/v1/chat/completions`.

- [ ] **Step 1: Write the failing test**

```python
def test_mistral_registered_and_keyguard(monkeypatch=None):
    import os
    from scourgify import classify
    assert "mistral" in classify.ENGINES and "mistral" in classify.PRICING
    os.environ.pop("MISTRAL_API_KEY", None)
    try:
        classify.ENGINES["mistral"]("", 60); assert False, "expected SystemExit"
    except SystemExit as e:
        assert "MISTRAL_API_KEY" in str(e)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run tests/test_promote.py`
Expected: FAIL — `KeyError: 'mistral'`

- [ ] **Step 3: Add the class** after `Gemini` (before the `ENGINES = {...}` line):

```python
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
```

- [ ] **Step 4: Register it.** Change line 202 to `ENGINES = {"apple": Apple, "claude": Claude, "openai": OpenAI, "gemini": Gemini, "mistral": Mistral}` and add `"mistral": (0.20, 0.60)` to `PRICING` (line 43). Update the module docstring engine list (line 11) to mention `mistral = Mistral (MISTRAL_API_KEY)`.

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run tests/test_promote.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/scourgify/classify.py tests/test_promote.py
git commit -m "feat(classify): add Mistral engine (works for classify and promote alike)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `parse_decision` in `promote.py`

**Files:**
- Create: `src/scourgify/promote.py`
- Test: `tests/test_promote.py`

**Interfaces:**
- Produces: `parse_decision(text) -> dict|None`. Extracts the first `{...}` JSON object (tolerant of markdown fences/prose), validates `verdict in {promote,alias,reject}`, requires non-empty `target` when `verdict=="alias"`, coerces `confidence` to one of `low|med|high` (default `med`). Returns `None` on any failure.

- [ ] **Step 1: Write the failing test**

```python
def test_parse_decision():
    from scourgify.promote import parse_decision
    assert parse_decision('{"verdict":"promote","reason":"novel","confidence":"high"}') == \
        {"verdict": "promote", "target": "", "reason": "novel", "confidence": "high"}
    # fenced + prose around it
    d = parse_decision('Sure!\n```json\n{"verdict":"alias","target":"Time Travel","reason":"same"}\n```')
    assert d["verdict"] == "alias" and d["target"] == "Time Travel" and d["confidence"] == "med"
    assert parse_decision('{"verdict":"alias","reason":"no target"}') is None    # alias needs target
    assert parse_decision('{"verdict":"maybe"}') is None                          # bad verdict
    assert parse_decision("not json") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run tests/test_promote.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'scourgify.promote'`

- [ ] **Step 3: Create `promote.py`** with the header, imports, constants (from the File Structure block above), and:

```python
#!/usr/bin/env python3
"""scourgify promote — adversarially decide whether each novel tag candidate from classify's
proposed_new list should be promoted to the vocab, aliased to an existing tag, or rejected.

  scourgify promote                 # dry run -> data/promote_review.csv (advocate + skeptic)
  scourgify promote --apply         # fold verdicts into overrides/ (vocab, tropes, aliases)
  scourgify promote --verify-with openai   # run the skeptic on a different engine (cross-model)

Reasons each candidate against a difflib shortlist of the master tag list (curated vocab ∪ ao3_vocab)
plus the example books that proposed it. Audit-first: verdicts are a reviewed artifact you apply."""
import argparse, csv, json, os, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib

from scourgify.classify import RANK, PROP, ENGINES, existing_terms, ask_retry
from scourgify.common import DATA, library

LEDGER = f"{DATA}/promote_ledger.csv"
REVIEW = f"{DATA}/promote_review.csv"
ALIASES = os.path.join(os.getcwd(), "overrides", "promote_aliases.csv")
VERDICTS = ("promote", "alias", "reject")


def parse_decision(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m: return None
    try: obj = json.loads(m.group(0))
    except Exception: return None
    v = str(obj.get("verdict", "")).strip().lower()
    if v not in VERDICTS: return None
    target = str(obj.get("target", "")).strip()
    if v == "alias" and not target: return None
    conf = str(obj.get("confidence", "med")).strip().lower()
    if conf not in ("low", "med", "high"): conf = "med"
    return {"verdict": v, "target": target if v == "alias" else "",
            "reason": str(obj.get("reason", "")).strip()[:200], "confidence": conf}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run tests/test_promote.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/scourgify/promote.py tests/test_promote.py
git commit -m "feat(promote): parse_decision — tolerant verdict-JSON extraction

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `shortlist` + prompt builders

**Files:**
- Modify: `src/scourgify/promote.py`
- Test: `tests/test_promote.py`

**Interfaces:**
- Produces: `shortlist(tag, existing=None, n=15) -> list[str]` (nearest master tags by `difflib`, `existing` defaults to `existing_terms()`); `advocate_prompt(cand, near) -> str`; `skeptic_prompt(cand, proposed, near) -> str`. `cand` is a dict `{tag, count, examples: [str,...]}`; `proposed` is a decision dict.

- [ ] **Step 1: Write the failing test**

```python
def test_shortlist_and_prompts():
    from scourgify.promote import shortlist, advocate_prompt, skeptic_prompt
    existing = ["Time Travel", "Fluff", "Angst", "Post-Apocalypse", "Slow Burn"]
    near = shortlist("Post-Apocalyptic", existing, n=3)
    assert "Post-Apocalypse" in near and len(near) <= 3       # true synonym surfaced despite low string sim
    cand = {"tag": "Post-Apocalyptic", "count": 4, "examples": ["A ruined world story"]}
    ap = advocate_prompt(cand, near)
    assert "Post-Apocalyptic" in ap and "Post-Apocalypse" in ap and "A ruined world story" in ap
    sp = skeptic_prompt(cand, {"verdict": "promote", "reason": "novel"}, near)
    assert "refute" in sp.lower() and "Post-Apocalypse" in sp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run tests/test_promote.py`
Expected: FAIL — `ImportError: cannot import name 'shortlist'`

- [ ] **Step 3: Add to `promote.py`:**

```python
def shortlist(tag, existing=None, n=15):
    existing = existing_terms() if existing is None else existing
    elow = {e.lower(): e for e in existing}
    return [elow[k] for k in difflib.get_close_matches(tag.lower(), list(elow), n=n, cutoff=0.0)]

_SCHEMA = ('Return ONLY a JSON object: {"verdict": "promote"|"alias"|"reject", '
           '"target": "<existing tag>" (required iff alias), '
           '"reason": "<one sentence>", "confidence": "low"|"med"|"high"}.')

def _ctx(cand, near):
    ex = " | ".join(cand.get("examples", [])[:5])[:600]
    return (f'CANDIDATE TAG: "{cand["tag"]}" (proposed for {cand["count"]} book(s))\n'
            f"NEAREST EXISTING MASTER TAGS: {', '.join(near)}\n"
            f"EXAMPLE BOOKS THAT USED IT: {ex}\n")

def advocate_prompt(cand, near):
    return ("You curate a controlled fanfiction tag vocabulary. Decide whether this NEW candidate tag "
            "should be PROMOTED (a genuinely new, reusable trope/theme not covered by an existing tag), "
            "ALIASED to one of the existing tags (same meaning, different words), or REJECTED "
            "(plot-specific, a character/fandom name, or noise).\n\n" + _ctx(cand, near) + "\n" + _SCHEMA)

def skeptic_prompt(cand, proposed, near):
    return ("You are a SKEPTIC. Another curator proposed the verdict below. Try to REFUTE a promote: "
            "is there an existing master tag that already covers this candidate (=> alias), or is it "
            "plot-specific / a character or fandom name / noise (=> reject)? Default to skeptical when "
            "unsure.\n\n" + _ctx(cand, near) +
            f'\nPROPOSED VERDICT: {proposed.get("verdict")} — {proposed.get("reason","")}\n\n' + _SCHEMA)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run tests/test_promote.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/scourgify/promote.py tests/test_promote.py
git commit -m "feat(promote): shortlist retrieval + advocate/skeptic prompt builders

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `candidates` (join example books + skip ledger)

**Files:**
- Modify: `src/scourgify/promote.py`
- Test: `tests/test_promote.py`

**Interfaces:**
- Produces: `candidates(ranked_path=RANK, proposal_path=PROP, ledger_path=LEDGER) -> list[dict]` each `{tag, count, examples: [title,...]}`, sorted by count desc, **excluding** any tag already in the ledger. `_ledger_tags(path) -> set[str]` helper.

- [ ] **Step 1: Write the failing test**

```python
def test_candidates_join_and_ledger_skip(tmp=None):
    import csv, os, tempfile
    from scourgify.promote import candidates
    d = tempfile.mkdtemp()
    ranked = os.path.join(d, "r.csv"); prop = os.path.join(d, "p.csv"); ledger = os.path.join(d, "l.csv")
    with open(ranked, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["proposed_tag", "count"])
        w.writerow(["Gacha Mechanic", "2"]); w.writerow(["Amoral Deity", "1"]); w.writerow(["Old Tag", "3"])
    with open(prop, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["book_id", "title", "added_tags", "proposed_new"])
        w.writerow(["1", "Rolls of Fate", "", "Gacha Mechanic"])
        w.writerow(["2", "Cruel God", "", "Amoral Deity; Gacha Mechanic"])
    with open(ledger, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tag", "verdict", "target"]); w.writerow(["Old Tag", "reject", ""])
    cs = candidates(ranked, prop, ledger)
    assert [c["tag"] for c in cs] == ["Gacha Mechanic", "Amoral Deity"]        # Old Tag skipped, count-sorted
    assert cs[0]["count"] == 2 and set(cs[0]["examples"]) == {"Rolls of Fate", "Cruel God"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run tests/test_promote.py`
Expected: FAIL — `ImportError: cannot import name 'candidates'`

- [ ] **Step 3: Add to `promote.py`:**

```python
def _ledger_tags(path):
    if not os.path.exists(path): return set()
    return {r["tag"] for r in csv.DictReader(open(path))}

def candidates(ranked_path=RANK, proposal_path=PROP, ledger_path=LEDGER):
    if not os.path.exists(ranked_path):
        raise SystemExit(f"no candidates ({os.path.basename(ranked_path)} not found — run a classify pass first).")
    decided = _ledger_tags(ledger_path)
    examples = {}                                              # tag -> [titles]
    if os.path.exists(proposal_path):
        for r in csv.DictReader(open(proposal_path)):
            for t in (r.get("proposed_new", "") or "").split("; "):
                t = t.strip()
                if t: examples.setdefault(t, []).append(r.get("title", ""))
    out = []
    for r in csv.DictReader(open(ranked_path)):
        tag = r["proposed_tag"].strip()
        if not tag or tag in decided: continue
        out.append({"tag": tag, "count": int(r.get("count", 0) or 0),
                    "examples": [t for t in examples.get(tag, []) if t]})
    out.sort(key=lambda c: -c["count"])
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run tests/test_promote.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/scourgify/promote.py tests/test_promote.py
git commit -m "feat(promote): candidates() — join example books, skip decided (ledger)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `decide` — the adversarial reasoning

**Files:**
- Modify: `src/scourgify/promote.py`
- Test: `tests/test_promote.py`

**Interfaces:**
- Produces: `decide(cand, ask, verify_ask=None, existing=None) -> dict` (the full decision dict from the shape block: `tag,count,verdict,target,reason,confidence,contested`). `ask`/`verify_ask` are callables `(prompt)->text` (injected in tests; in production they wrap `ask_retry(eng, ...)[0]`). Logic: advocate proposes; if `promote`, the skeptic (via `verify_ask or ask`) may override to `alias`/`reject` (skeptic wins, `contested=True`); advocate `alias`/`reject` is accepted as-is. Unparseable advocate → `reject`/low.

- [ ] **Step 1: Write the failing test**

```python
def test_decide_reconciliation():
    from scourgify.promote import decide
    cand = {"tag": "Amoral Deity", "count": 1, "examples": ["A cruel god toys with mortals"]}
    near = ["Morality", "Deity", "Dark"]
    # advocate promotes, skeptic refutes -> reject, contested
    adv = lambda p: '{"verdict":"promote","reason":"seems new","confidence":"med"}'
    sk = lambda p: '{"verdict":"reject","reason":"too plot-specific","confidence":"high"}'
    calls = iter([adv, sk])
    ask = lambda p: next(calls)(p)
    d = decide(cand, ask, existing=near)
    assert d["verdict"] == "reject" and d["contested"] is True and d["tag"] == "Amoral Deity"

    # advocate aliases -> accepted directly, not contested (skeptic not consulted)
    d2 = decide(cand, lambda p: '{"verdict":"alias","target":"Morality","reason":"same idea"}', existing=near)
    assert d2["verdict"] == "alias" and d2["target"] == "Morality" and d2["contested"] is False

    # promote survives skepticism
    seq = iter(['{"verdict":"promote","reason":"novel"}', '{"verdict":"promote","reason":"agree, novel"}'])
    d3 = decide(cand, lambda p: next(seq), existing=near)
    assert d3["verdict"] == "promote" and d3["contested"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run tests/test_promote.py`
Expected: FAIL — `ImportError: cannot import name 'decide'`

- [ ] **Step 3: Add to `promote.py`:**

```python
def decide(cand, ask, verify_ask=None, existing=None):
    near = shortlist(cand["tag"], existing)
    base = {"tag": cand["tag"], "count": cand.get("count", 0)}
    adv = parse_decision(ask(advocate_prompt(cand, near)))
    if adv is None:
        return {**base, "verdict": "reject", "target": "", "contested": False,
                "reason": "advocate response unparseable", "confidence": "low"}
    if adv["verdict"] != "promote":
        return {**base, **adv, "contested": False}                 # alias/reject accepted as proposed
    sk = parse_decision((verify_ask or ask)(skeptic_prompt(cand, adv, near)))
    if sk and sk["verdict"] in ("alias", "reject"):
        return {**base, **sk, "contested": True}                   # skeptic refuted the promote
    return {**base, **adv, "contested": False}                     # promote stands
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run tests/test_promote.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/scourgify/promote.py tests/test_promote.py
git commit -m "feat(promote): decide() — advocate proposes, skeptic refutes, human referees

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `apply_decisions` — fold verdicts, archive, ledger

**Files:**
- Modify: `src/scourgify/promote.py`
- Test: `tests/test_promote.py`

**Interfaces:**
- Produces: `apply_decisions(review_path=REVIEW, vocab_path=<overrides vocab>, tropes_path=<overrides tropes>, aliases_path=ALIASES, ledger_path=LEDGER) -> dict` counts. **promote** → append `tag` to `vocab_path`; **alias** → append `tag,target,tag` to `tropes_path` (semicolon-delimited to match `overrides/tropes.csv`) AND `tag,target` to `aliases_path`; **all** → append `tag,verdict,target` to `ledger_path`. Archive `review_path` → `promote_review_applied_<ts>.csv`. Files are created with headers if absent; appends never rewrite existing rows.

- [ ] **Step 1: Write the failing test**

```python
def test_apply_decisions_routing():
    import csv, os, tempfile
    from scourgify.promote import apply_decisions
    d = tempfile.mkdtemp()
    review = os.path.join(d, "review.csv"); vocab = os.path.join(d, "vocab.txt")
    tropes = os.path.join(d, "tropes.csv"); aliases = os.path.join(d, "aliases.csv"); ledger = os.path.join(d, "l.csv")
    with open(review, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tag", "count", "verdict", "target", "reason", "confidence", "contested"])
        w.writerow(["Gacha Mechanic", "2", "promote", "", "novel", "high", "False"])
        w.writerow(["Amoral Deity", "1", "alias", "Morality", "same", "med", "True"])
        w.writerow(["Chapter 3 Spoiler", "1", "reject", "", "plot", "high", "False"])
    n = apply_decisions(review, vocab, tropes, aliases, ledger)
    assert n == {"promote": 1, "alias": 1, "reject": 1}
    assert "Gacha Mechanic" in open(vocab).read()
    trows = list(csv.reader(open(tropes), delimiter=";"))
    assert ["Amoral Deity", "Morality", "tag"] in trows
    assert ["Amoral Deity", "Morality"] in list(csv.reader(open(aliases)))
    ledger_tags = {r["tag"] for r in csv.DictReader(open(ledger))}
    assert ledger_tags == {"Gacha Mechanic", "Amoral Deity", "Chapter 3 Spoiler"}
    assert not os.path.exists(review)                                  # archived away
    assert any(x.startswith("review_applied_") or "applied" in x for x in os.listdir(d))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run tests/test_promote.py`
Expected: FAIL — `ImportError: cannot import name 'apply_decisions'`

- [ ] **Step 3: Add to `promote.py`:**

```python
def _append_line(path, line):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f: f.write(line + "\n")

def _append_row(path, header, row, delim=","):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f, delimiter=delim)
        if new: w.writerow(header)
        w.writerow(row)

def apply_decisions(review_path=REVIEW, vocab_path=None, tropes_path=None,
                    aliases_path=ALIASES, ledger_path=LEDGER):
    vocab_path = vocab_path or os.path.join(os.getcwd(), "overrides", "classify_vocab.txt")
    tropes_path = tropes_path or os.path.join(os.getcwd(), "overrides", "tropes.csv")
    if not os.path.exists(review_path):
        raise SystemExit(f"no review to apply ({os.path.basename(review_path)} not found — run promote first).")
    n = {"promote": 0, "alias": 0, "reject": 0}
    for r in csv.DictReader(open(review_path)):
        v, tag, target = r["verdict"], r["tag"], r.get("target", "")
        if v == "promote":
            _append_line(vocab_path, tag)
        elif v == "alias":
            _append_row(tropes_path, ["variant", "canonical", "route"], [tag, target, "tag"], delim=";")
            _append_row(aliases_path, ["candidate", "target"], [tag, target])
        n[v] = n.get(v, 0) + 1
        _append_row(ledger_path, ["tag", "verdict", "target"], [tag, v, target])
    arch = review_path.replace(".csv", f"_applied_{time.strftime('%Y%m%d-%H%M%S')}.csv")
    os.rename(review_path, arch)
    print(f"applied: {n['promote']} promoted, {n['alias']} aliased, {n['reject']} rejected; "
          f"review archived -> {os.path.basename(arch)}")
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run tests/test_promote.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/scourgify/promote.py tests/test_promote.py
git commit -m "feat(promote): apply_decisions — vocab/tropes/aliases routing + ledger + archive

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Applied-alias hard-map feeds `parse_resp`

**Files:**
- Modify: `src/scourgify/classify.py` (`parse_resp` ~lines 98-119; add `load_aliases` + a module cache near `load_vocab`)
- Test: `tests/test_promote.py`

**Interfaces:**
- Produces: `classify.load_aliases() -> dict` reading `overrides/promote_aliases.csv` (`candidate->target`, `{}` if absent, cached in `_ALIASES`). `parse_resp` consults it: a `new` tag whose lowercased form is an alias key snaps to `target` (applied as a vocab tag if `target` is in vocab; otherwise dropped from `new`). Snap happens **before** the difflib near-miss check.

- [ ] **Step 1: Write the failing test**

```python
def test_parse_resp_applied_alias_snap(tmp=None):
    import os, tempfile, csv
    from scourgify import classify
    d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "overrides"))
    with open(os.path.join(d, "overrides", "promote_aliases.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["candidate", "target"]); w.writerow(["Post-Apocalyptic", "Angst"])
    old = os.getcwd(); os.chdir(d); classify._ALIASES = None; classify._VOCAB = None
    try:
        vt, nt = classify.parse_resp('{"tags": [], "new": ["Post-Apocalyptic"]}')
        assert "Angst" in vt          # snapped to the aliased vocab term, applied
        assert "Post-Apocalyptic" not in nt
    finally:
        os.chdir(old); classify._ALIASES = None; classify._VOCAB = None
```
(Note: `Angst` is a real bundled vocab term, so the snap resolves to an applied tag.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run tests/test_promote.py`
Expected: FAIL — `AttributeError: module 'scourgify.classify' has no attribute '_ALIASES'`

- [ ] **Step 3: Add the loader** near `load_vocab` (after line 60) in `classify.py`:

```python
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
```

- [ ] **Step 4: Wire it into `parse_resp`.** In the `for t in obj.get("new", []):` loop (line 110), immediately after the `if tl in vlow: keep(vlow[tl]); continue` line (114), insert:

```python
        al = load_aliases().get(tl)
        if al is not None:                          # a decided synonym: snap to vocab, else drop
            if al.lower() in vlow: keep(vlow[al.lower()])
            continue
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run tests/test_promote.py && uv run tests/test_core.py`
Expected: both PASS (no alias file in the repo → `load_aliases()` returns `{}`, existing behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/scourgify/classify.py tests/test_promote.py
git commit -m "feat(classify): parse_resp snaps decided aliases so the pool shrinks over runs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `promote` CLI (run loop) + `cli.py` dispatch

**Files:**
- Modify: `src/scourgify/promote.py` (add `build_parser`, `normalize`, `run`, `main`)
- Modify: `src/scourgify/cli.py` (add a `promote` branch)
- Test: `tests/test_promote.py`

**Interfaces:**
- Produces: `promote.main()`; `promote.build_parser() -> ArgumentParser` with `--engine` (choices `sorted(ENGINES)`, default `claude`), `--verify-with` (choices `sorted(ENGINES)`, default `""`), `--model`, `--workers` (default 8), `--batch` (default 0), `--limit` (default 0), `--timeout` (default 60), `--yes/-y`, `--apply`. `run(a)` builds the review CSV via `decide` over `candidates()` using a `ThreadPoolExecutor`; writes `REVIEW`. `cli.main` routes `argv[0]=="promote"` to `promote.main()`.

- [ ] **Step 1: Write the failing test** (fake engine registered in `ENGINES`, exercises the run loop end-to-end without network)

```python
def test_promote_run_writes_review(tmp=None):
    import os, csv, tempfile
    from scourgify import classify, promote
    class Fake:                                     # advocate promotes, skeptic agrees -> promote stands
        def __init__(self, model, timeout): pass
        def ask(self, prompt): return '{"verdict":"promote","reason":"novel reusable trope","confidence":"high"}'
    classify.ENGINES["fake"] = Fake
    d = tempfile.mkdtemp()
    ranked = os.path.join(d, "r.csv"); prop = os.path.join(d, "p.csv")
    with open(ranked, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["proposed_tag", "count"]); w.writerow(["Reality Warping", "3"])
    with open(prop, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["book_id", "title", "added_tags", "proposed_new"]); w.writerow(["1", "Bend It", "", "Reality Warping"])
    review = os.path.join(d, "promote_review.csv")
    a = promote.build_parser().parse_args(["--engine", "fake", "--yes"])
    promote.run(a, ranked_path=ranked, proposal_path=prop, review_path=review, existing=["Time Travel", "Fluff"])
    rows = list(csv.DictReader(open(review)))
    assert len(rows) == 1 and rows[0]["tag"] == "Reality Warping" and rows[0]["verdict"] == "promote"
    del classify.ENGINES["fake"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run tests/test_promote.py`
Expected: FAIL — `AttributeError: module 'scourgify.promote' has no attribute 'build_parser'`

- [ ] **Step 3: Add to `promote.py`:**

```python
REVIEW_COLS = ["tag", "count", "verdict", "target", "reason", "confidence", "contested"]

def run(a, ranked_path=RANK, proposal_path=PROP, review_path=REVIEW, existing=None):
    cands = candidates(ranked_path, proposal_path)
    if a.limit: cands = cands[:a.limit]
    if a.batch: cands = cands[:a.batch]
    if not cands:
        print("no undecided candidates — nothing to do."); return
    eng = ENGINES[a.engine](a.model, a.timeout)
    veng = ENGINES[a.verify_with](a.model, a.timeout) if a.verify_with else None
    ask = lambda p: ask_retry(eng, p)[0]
    verify_ask = (lambda p: ask_retry(veng, p)[0]) if veng else None
    print(f"engine={a.engine}{'  verify-with='+a.verify_with if veng else ''}  candidates: {len(cands)}")
    rows = []
    with ThreadPoolExecutor(max_workers=1 if a.engine == "apple" else a.workers) as ex:
        futs = [ex.submit(decide, c, ask, verify_ask, existing) for c in cands]
        for fut in as_completed(futs): rows.append(fut.result())
    rows.sort(key=lambda r: (r["verdict"] != "promote", -r["count"]))   # promotes first, by count
    os.makedirs(DATA, exist_ok=True)
    with open(review_path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(REVIEW_COLS)
        for r in rows: w.writerow([r.get(k, "") for k in REVIEW_COLS])
    tally = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    print(f"  {tally['promote']} promote, {tally['alias']} alias, {tally['reject']} reject "
          f"-> {os.path.basename(review_path)} (review, then `scourgify promote --apply`)")

def build_parser():
    p = argparse.ArgumentParser(description="Adversarially decide promote/alias/reject for classify's proposed-new tags.")
    p.add_argument("--engine", default="claude", choices=sorted(ENGINES))
    p.add_argument("--verify-with", default="", choices=[""] + sorted(ENGINES),
                   help="run the skeptic on a different engine (cross-model check)")
    p.add_argument("--model", default="")
    p.add_argument("--workers", type=int, default=8, metavar="N")
    p.add_argument("--batch", type=int, default=0, metavar="N")
    p.add_argument("--limit", type=int, default=0, metavar="N")
    p.add_argument("--timeout", type=int, default=60, metavar="S")
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--apply", action="store_true", help="fold data/promote_review.csv into overrides/")
    return p

def normalize(a):
    library()                                       # fail fast with the clear CALIBRE_LIBRARY message
    os.makedirs(DATA, exist_ok=True)
    return a

def main():
    a = normalize(build_parser().parse_args())
    if a.apply: apply_decisions()
    else: run(a)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Wire `cli.py`.** After the `staleness` branch (line 24) add:

```python
    if argv and argv[0] == "promote":
        from scourgify import promote
        sys.argv = ["scourgify promote", *argv[1:]]
        return promote.main()
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run tests/test_promote.py && uv run tests/test_core.py`
Expected: both PASS

- [ ] **Step 6: Commit**

```bash
git add src/scourgify/promote.py src/scourgify/cli.py tests/test_promote.py
git commit -m "feat(promote): run loop + CLI + cli.py dispatch (scourgify promote)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Documentation

**Files:**
- Modify: `README.md` (the `## Content-based tagging` section — after the proposal/promote bullet), `CLAUDE.md` (the `classify.py` architecture paragraph + the maintenance-loop block)

**Interfaces:** none (docs).

- [ ] **Step 1: README** — add, under the classify section, a subsection:

```markdown
### Growing the vocab — `scourgify promote`
After a classify run, novel tag candidates land in `classify_newtags_ranked.csv`. `scourgify promote`
adjudicates each one **adversarially against the master tag list**: an advocate proposes
promote / alias / reject, a skeptic (optionally a *different* engine via `--verify-with openai`) tries
to refute a promote, and the difflib shortlist of nearest master tags grounds both. Verdicts land in
`data/promote_review.csv` for review; `scourgify promote --apply` folds them into `overrides/`
(promotes → vocab, aliases → `tropes.csv` + a snap-map that stops re-proposal). Engines and keys are
exactly classify's (`--engine claude|openai|gemini|mistral|apple`; your own API key in the env; `apple`
is free/on-device). Grow the *shipped* vocab by running it with a cloud engine from the repo and
committing the `defaults/classify_vocab.txt` diff.
```

- [ ] **Step 2: CLAUDE.md** — append to the `classify.py` paragraph: a sentence that `promote.py` reuses classify's engines/`ask_retry`/`existing_terms` to adjudicate `proposed_new` (advocate→skeptic, `--verify-with` for cross-model, human review is the referee), writes `data/promote_review.csv`, and `--apply` folds into `overrides/` + feeds `parse_resp`'s alias snap. Add `promote --incremental`-style line to the maintenance-loop block: `→ scourgify promote  # adjudicate new-tag candidates → review → promote --apply`.

- [ ] **Step 3: Verify docs render** (no build; just read back)

Run: `grep -n "scourgify promote" README.md CLAUDE.md`
Expected: the new references present.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: scourgify promote — adversarial vocab-growth step

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] `uv run tests/test_promote.py && uv run tests/test_core.py && uv run tests/test_selection.py && uv run tests/test_layers.py` — all green.
- [ ] Real dry run (needs `ANTHROPIC_API_KEY` or another cloud key; ~96 candidates, cheap): `uv run scourgify promote --engine claude --limit 10` → `data/promote_review.csv` written; spot-check that `Post-Apocalyptic` aliases to `Post-Apocalypse` and `Amoral Deity` is NOT aliased to `Morality` (the difflib failure cases the AI fixes).
- [ ] `uv run scourgify promote --apply` → vocab/tropes/aliases/ledger updated, review archived; a second `promote` run reports fewer candidates (ledger skip).
- [ ] `uv build && unzip -l dist/*.whl | grep promote` → `scourgify/promote.py` shipped; no `data/`/`overrides/` in the wheel.

## Self-review notes

- **Spec coverage:** shared core (candidates/shortlist/prompts/parse_decision/apply_decisions/ledger) → Tasks 3-7,9; `ask_retry` refactor → Task 1; Mistral + PRICING → Task 2; `--verify-with` → Tasks 6,9; `parse_resp` alias hard-map → Task 8; cli dispatch → Task 9; tests → every task; docs → Task 10. The dropped maintainer-Workflow shell is intentionally absent (spec revision).
- **Referee simplification:** the spec mentions a referee on disagreement; per the `/octo:debate` outcome this is implemented as `contested=True` (skeptic wins, human reviews) rather than a third LLM call — `--verify-with` is the cross-model version. Called out so it is not a silent deviation.
- **Type consistency:** the decision dict keys (`tag,count,verdict,target,reason,confidence,contested`) and `REVIEW_COLS` match across Tasks 6,7,9; `ask`/`verify_ask` are `(prompt)->text` callables throughout; `cand` is `{tag,count,examples}` in Tasks 4,5,6.
