#!/usr/bin/env python3
"""Settle every book's synopsis: a spoiler-safe back cover in Calibre's own description field.

    scourgify synopsis                       # dry run: resolve the queue, report it, send NOTHING
    scourgify synopsis --apply               # generate + write (Calibre CLOSED)
    scourgify synopsis --apply --step        # ...reviewing each new synopsis 1-by-1
    scourgify synopsis --apply --batch 100   # chew through the queue a chunk at a time

Two jobs per book, and the cheap one runs first: if the existing description is already an
adequate, spoiler-safe blurb the model says so in ONE word and the book is stamped with its
author's own text UNTOUCHED (never AI-flatten a library of fine descriptions into one beige
voice). Only a bad blurb earns the expensive path — read the book's own prose in slabs, note each,
then fold the notes into a back cover: premise, characters, stakes, hook, and a themes line.
Never plot outcomes, never endings; the description stays safe to browse.

**`#synopsized`** (datetime) is the entire state: "synopsis SETTLED on this date", earned either
way. It is the queue (unstamped = outstanding), the provenance, and the refresh clock (`#updated`
newer than the stamp = the fic grew chapters, so re-settle it). Queue membership itself lives in
`select.pick("unsynopsized")` and is never re-derived here.

The synopsis goes into the BUILT-IN description because improving it is half the point: Calibre
and every reader display that field. FanFicFare would overwrite it on a metadata re-fetch, so the
pass refuses to start unless FFF's Comments -> "New Only" switch is on (`--force` accepts the
degraded self-healing mode instead: a clobbered book simply re-enters the queue and is
re-summarized — wasted free compute, not lost data).

Engine: `apple` — free, on-device, single-threaded and slow, which is fine; this sweep is designed
to run in the background for weeks. A cloud engine is an explicit `--engine` opt-in for a book
that stalls, never a default. Unlike classify, a BARE run here costs nothing at all: there is no
intermediate proposal artifact to build, because resume is off the stamp."""
import argparse
import contextlib
import copy
import os
import re

from scourgify import booktext, select
from scourgify.artifacts import merge_failures, read_rows, syn_fail, write_failures
from scourgify.booktext import strip_html
from scourgify.common import (GuardrailError, custom_column_id, op_create_column, op_set_field,
                              op_stamp_now, ro_connect, run_writer, titles as book_titles)
from scourgify.engines import ENGINES, ask_retry
from scourgify.setup import comments_protected

STAMP = select.SYN_STAMP     # one name for the stamp; select owns the queue that reads it

# Measured 2026-08-23 against afm.swift with real EPUB prose: the on-device model's context is a
# HARD 4096 tokens shared by prompt AND answer, and English prose runs ~4.3 chars/token (18,000
# chars = 4,165 tokens = refused; 17,000 passes). 10k chars is ~2,300 tokens, leaving generous
# room for the template and the reply. Re-measure when the bundled model changes.
CHUNK = 10_000
MAX_CHUNKS = 12          # slabs actually read per book — see chunks()
NOTE_CAP = 500           # chars kept per slab note, so MAX_CHUNKS*NOTE_CAP fits ONE reduce prompt
EXTRACT = 2_000_000      # chars pulled from the file before sampling (bounds memory on a huge fic)
MIN_JUDGE = 120          # below this there is no blurb worth judging — go straight to generation
MIN_SYNOPSIS = 120       # a shorter answer than this is the model failing, not a back cover
JUDGE_CAP = 4_000        # blurb chars sent to the adequacy judge

# Framed as "is this ABOUT the story", not "is this GOOD" — and it says so out loud that almost
# all real descriptions pass. Measured 2026-08-23 on 20 random library books: the earlier
# good/bad-criteria phrasing rejected 12 of 13, including an 877-char blurb, which would have
# AI-rewritten thousands of healthy author descriptions (the one thing decision Q4 forbids) and
# turned a seconds-per-book sweep into days of generation. This phrasing keeps 12 of 20.
# Re-measure this ratio when the prompt or the bundled model changes — it is the pass's cost model.
JUDGE_P = (
    "Here is a fanfiction's description, as its author wrote it.\n\n"
    "Title: {title}\nDescription: {blurb}\n\n"
    "Does it tell a reader what the story is about? Almost all real descriptions do — answer NO "
    "only if this one is NOT about the story: an author's note, an update schedule, a list of "
    "tags or warnings, \"summary inside\", a link, or a single vague line that could describe "
    "anything.\n"
    "Answer with exactly one word: YES or NO.")

NOTE_P = (
    'Excerpt {i} of {n} from the fanfiction "{title}".\n'
    "In two sentences, note the characters, the setting, the situation and the tone here. "
    "Plain prose, no preamble.\n\n{chunk}")

BACK_P = (
    'Notes taken while reading the fanfiction "{title}", in order:\n\n{notes}\n\n'
    "Write its back-cover blurb: three to five sentences of flowing prose covering the premise, "
    "the main characters and what is at stake, ending on a hook. Then one final line reading "
    "'Themes: a, b, c' with three short theme or trope words.\n"
    "SPOILER-SAFE: never reveal how the story ends or how its conflicts resolve.\n"
    "No preamble, no title, no headings — the blurb only.")


def chunks(text: str, size: int = CHUNK, cap: int = MAX_CHUNKS) -> list:
    """`text` in <=size-char slabs; past `cap` slabs, an even spread that always keeps the first two.

    ponytail: even sampling rather than every chapter. The back cover needs the opening (premise,
    cast, hook — always slabs 0 and 1) plus a sense of the whole, and reading all ~300 slabs of a
    500k-word fic on-device costs ~10 minutes for ONE book against a 7,949-book library. Raise
    `cap` if generated synopses read thin; the ceiling here is wall-clock, not quality by design."""
    parts = [text[i:i + size] for i in range(0, len(text), size)]
    if len(parts) <= cap: return parts
    step = len(parts) / cap
    keep = sorted({0, 1} | {int(i * step) for i in range(cap)})[:cap]
    return [parts[i] for i in keep]


def clean(s) -> str:
    """One engine answer as storable prose: whitespace collapsed, a chatty lead-in dropped,
    surrounding quotes stripped. '' for an error line or nothing usable — and '' is exactly what
    the callers test, because handing '' to a set_field on `comments` is a WIPE, not a synopsis."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if not s or s.startswith("ERR"): return ""
    s = re.sub(r"^(sure[,!.]?\s*)?here(?:'s| is)[^:]{0,60}:\s*", "", s, flags=re.I)
    return s.strip().strip('"').strip()


def verdict(resp) -> str:
    """The adequacy answer as 'keep' | 'generate' | '' (unreadable). Pure.

    Three states, not two, because a guess is wrong in BOTH directions: guessing 'keep' stamps a
    bad blurb as settled forever, and guessing 'generate' AI-rewrites an author's healthy prose.
    '' settles nothing — the book earns a failure row and is retried later, on another engine if
    need be. Only a leading YES/NO counts; the prompt asks for exactly one word."""
    s = str(resp or "").strip()
    if re.match(r"\W*yes\b", s, re.I): return "keep"
    if re.match(r"\W*no\b", s, re.I): return "generate"
    return ""


def guard_comments(con, force: bool = False) -> None:
    """Pre-flight: refuse to start unless FanFicFare cannot overwrite what we are about to write.

    GuardrailError, never SystemExit — this is reachable from a Calibre job, where a SystemExit
    would kill the worker thread silently. It runs before any GENERATION, not before the write:
    the point is not to spend hours of on-device compute on synopses a re-fetch will erase."""
    if force: return
    if comments_protected(con) is False:
        raise GuardrailError(
            "FanFicFare would overwrite every synopsis this pass writes.\n"
            "  The synopsis lives in the built-in description (Comments), and FFF re-fetches it.\n"
            "  Fix: Calibre -> Preferences -> Plugins -> FanFicFare -> Customize -> Standard Columns\n"
            "       -> tick 'New Only' beside Comments.   (`scourgify setup` offers the same fix.)\n"
            "  --force accepts the degraded mode instead: a clobbered book re-enters the queue and "
            "is re-summarized — wasted free compute, not lost data.")


def settle(title: str, blurb: str, path, ask) -> tuple:
    """One book -> (synopsis, error).

    ('', '')   the existing blurb is adequate — SETTLED, nothing to write, author voice kept.
    (text, '') a generated back cover to write into the description.
    ('', err)  attempted and blocked; the caller files a failure row so the queue stays finite.

    `ask` is prompt -> (text, err) — the injected seam (engines.ask_retry in production, a fake in
    tests), the same shape as promote.run(ask=) and classify.Plan.run(ask=)."""
    if len(blurb) >= MIN_JUDGE:
        v, err = ask(JUDGE_P.format(title=title, blurb=blurb[:JUDGE_CAP]))
        if err: return "", err
        d = verdict(v)
        if d == "keep": return "", ""
        if not d: return "", f"parse: no yes/no in the adequacy verdict ({clean(v)[:80]!r})"
    text = booktext.extract(path, limit=EXTRACT)
    if not text: return "", "no readable text (file missing, DRM'd, or empty)"
    slabs = chunks(text)
    notes = []
    for i, c in enumerate(slabs, 1):
        n, err = ask(NOTE_P.format(i=i, n=len(slabs), title=title, chunk=c))
        if err: return "", err
        n = clean(n)
        if n: notes.append(n[:NOTE_CAP])
    if not notes: return "", "engine returned nothing usable for any excerpt"
    out, err = ask(BACK_P.format(title=title, notes="\n".join(f"- {n}" for n in notes)))
    if err: return "", err
    out = clean(out)
    return (out, "") if len(out) >= MIN_SYNOPSIS else ("", "engine returned no usable synopsis")


class Plan:
    """ONE resolved synopsis run: guard -> scope -> todo, computed once so a confirm and the work
    that follows it are over the same set (mirrors wrangle.Plan and classify.Plan; the CLI and the
    wizard drive the same object). Owns a COPY of the caller's options — steering a resolved plan
    goes through `p.opts`, never by mutating the caller's namespace."""

    def __init__(self, a: argparse.Namespace):
        self.opts = a = copy.copy(a)
        with contextlib.closing(ro_connect()) as con:
            guard_comments(con, a.force)   # before any work: hours of compute a re-fetch would erase
            if a.books is not None:
                ids = select.pick(con, "ids", ids=select.parse_books(a.books))
            elif a.last:
                ids = select.pick(con, "last", n=a.last)
            else:
                ids = select.pick(con, "unsynopsized")
            self.scope = ("named ids" if a.books is not None else
                          f"last {a.last} added" if a.last else "the synopsis queue")
            self.blurbs = {b: strip_html(t) for b, t in con.execute("SELECT book, text FROM comments")}
            self.files = booktext.paths(con)
            self.titles = book_titles(con)
            self.have_stamp = custom_column_id(con, STAMP) is not None
        self.todo = ids[:a.batch] if a.batch else ids

    def preview(self) -> None:
        """What a run WOULD do, without doing any of it. Unlike classify there is nothing to send
        for a proposal, so this is zero cost — not merely zero writes."""
        from scourgify import report
        n = len(self.todo)
        judged = sum(1 for b in self.todo if len(self.blurbs.get(b, "")) >= MIN_JUDGE)
        report.say(f"  scope: {self.scope} -> {n} book(s), engine={self.opts.engine}")
        report.table("what this run would do", ["books", "step"],
                     [[str(judged), "have a blurb to judge (one cheap call; a good one is kept as-is)"],
                      [str(n - judged), "go straight to whole-book generation"],
                      [str(n), f"stamped {STAMP} either way"]], right=(0,))
        report.say("\nDry run — nothing sent, nothing written. To run it: "
                   "scourgify synopsis --apply   (Calibre closed)")

    def run(self, ask=None) -> None:
        """Execute. Without --apply this is preview() and stops — see the module docstring."""
        from scourgify import report
        a = self.opts
        if not a.apply:
            self.preview(); return
        if not self.todo:
            report.say("every book's synopsis is settled ✓"); return
        if ask is None:
            eng = ENGINES[a.engine](a.model, a.timeout)
            ask = lambda prompt: ask_retry(eng, prompt)
        report.say(f"  {len(self.todo)} book(s) · engine={a.engine} "
                   f"({'free, on-device — slow is fine' if a.engine == 'apple' else 'billed per book'})")
        made, kept, failures = {}, [], []
        for i, b in enumerate(self.todo, 1):
            title = str(self.titles.get(b, ""))
            out, err = settle(title, self.blurbs.get(b, ""), self.files.get(b), ask)
            if err:
                failures.append([b, title, err]); mark = f"✗ {err[:60]}"
            elif out:
                made[b] = out; mark = f"wrote {len(out)} chars"
            else:
                kept.append(b); mark = "kept the existing blurb"
            report.say(f"  [{i}/{len(self.todo)}] #{b} {title[:40]:<40} {mark}")
        # The log is rewritten every run, not only when this one failed: a book recovered on
        # another engine has to LEAVE the list or it reads as blocked forever — and stays out of
        # the queue with it. Books outside this run's scope are carried through untouched.
        processed = set(made) | set(kept) | {r[0] for r in failures}
        write_failures(merge_failures(read_rows(syn_fail()), processed, failures), syn_fail())
        if failures:
            report.say(f"  {len(failures)} failed -> {os.path.basename(syn_fail())}  "
                       "(retry on another engine: scourgify synopsis --apply --engine openai)")
        if a.step and made:
            made = step(made, self.titles)
        if not (made or kept):
            report.say("(nothing settled — nothing written.)"); return
        ops = []
        if not self.have_stamp:
            ops.append(op_create_column("synopsized", "Synopsized", "datetime"))
        if made:
            ops.append(op_set_field("comments", made))
        # Stamp EVERY settled book, generated or kept — an unstamped kept blurb would be re-read
        # and re-judged on every future sweep, which is classify's no-tag bug in a new field.
        ops.append(op_stamp_now(STAMP, sorted(set(made) | set(kept))))
        run_writer(ops, tool="synopsis", scope=f"{len(made)} written, {len(kept)} kept")
        report.say(f"settled {len(made) + len(kept)} book(s): {len(made)} new synopses, "
                   f"{len(kept)} existing blurbs kept.")


def options(n: int) -> list:
    """PURE half of the synopsis menu (relocated from wizard._synopsis_options — FOUND-06/D-10):
    one fixed slot layout whatever the queue holds."""
    return [
        ("1", "apply" if n else None, f"settle {n:,} books" if n else "settle — nothing outstanding",
         "judge each existing blurb; keep the good ones untouched, write a back cover for the rest"
         if n else "every book's synopsis is already settled"),
        ("2", "step" if n else None, "review 1-by-1",
         "generate first, then walk each NEW synopsis; untick to leave that description alone"
         if n else "nothing to walk"),
        ("3", "skip", "skip", "leave descriptions unchanged (it is free, but slow — a chunk at a time is fine)"),
    ]


def step(made: dict, titles: dict) -> dict:
    """1-by-1 review of the generated synopses -> the ACCEPTED subset ({} = nothing decided).

    Lives here, not in the wizard: CLAUDE.md's rule is that a wizard stage calls the same engine
    function the subcommand does, so `synopsis --apply --step` and the wizard share one path. An
    unticked book gets neither its new description NOR the stamp — it stays in the queue, which is
    the point of rejecting it."""
    from scourgify import ui
    if not ui.interactive():
        raise GuardrailError("--step needs an interactive terminal (omit it to write every synopsis).")
    ids = sorted(made)
    acc, _, action = ui.checklist(
        "new synopses — untick one to leave that book's description alone",
        [f"[bold]#{b}[/] {str(titles.get(b, ''))[:36]:<36} [dim]{made[b][:120]}…[/]" for b in ids])
    if action in ("skip", "quit"): return {}
    return {ids[i]: made[ids[i]] for i in acc}


def plan(a: argparse.Namespace) -> Plan:
    """Resolve a synopsis run ONCE — the wizard confirms over this plan and run() executes the
    SAME one."""
    return Plan(a)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Settle every book's synopsis into its description (free + on-device by "
                    "default; dry-run until --apply).")
    p.add_argument("--apply", action="store_true",
                   help="generate and write (Calibre closed). Without it NOTHING is sent — unlike "
                        "classify, a bare run here really is free.")
    p.add_argument("--step", action="store_true",
                   help="with --apply: review each new synopsis 1-by-1 (untick to leave a description alone)")
    p.add_argument("--engine", default="apple", choices=sorted(ENGINES),
                   help="apple = on-device, free, slow (default; the engine this pass is designed for)")
    p.add_argument("--books", default=None, metavar="SPEC",
                   help="only these books: '1,2,3', '10-20', '@ids.txt', or a combination "
                        "(re-settles them whatever their stamp says)")
    p.add_argument("--last", type=int, default=0, metavar="N",
                   help="only the N most recently added books (the same N as classify --last)")
    p.add_argument("--batch", type=int, default=0, metavar="N",
                   help="settle only N books this run — the queue advances, so re-run to continue")
    p.add_argument("--force", action="store_true",
                   help="run even though FanFicFare may overwrite the descriptions (a clobbered "
                        "book re-enters the queue and is re-summarized)")
    p.add_argument("--model", default="", help="override the per-engine default model")
    p.add_argument("--timeout", type=int, default=120, metavar="S", help="per-request HTTP timeout")
    return p


def default_opts(**overrides) -> argparse.Namespace:
    """The non-CLI entry to a run's options: parser defaults + keyword overrides. The argparse
    parser stays the single schema; the wizard is the second adapter that fills it."""
    a = build_parser().parse_args([])
    for k, v in overrides.items(): setattr(a, k, v)
    return a


def main() -> None:
    a = build_parser().parse_args()
    if a.books is not None and a.last:
        raise GuardrailError("--books and --last are two ways to name the same thing — pick one.")
    plan(a).run()


if __name__ == "__main__":
    main()
