#!/usr/bin/env python3
"""One-command guided wizard over the whole toolchain:

    scourgify                    # no arguments — launches this interactive wizard

A status header (books, column health, new/changed count, pending proposal),
setup if the library needs it, then a landing menu (landing_menu) that asks what
to do: the full guided maintenance run in the right order — wrangle → staleness →
classify → review → promote → backfill — or a single task, with unfinished work
flagged inline; the menu loops until quit. Every stage dry-runs first, shows its
report, and asks before writing. Writes refuse while Calibre is open and
auto-back-up metadata.db (everything funnels through common.run_writer). Single
steps stay available as CLI subcommands: scourgify setup / audit / apply /
classify / staleness. This module has no main()/argparse entry of its own — it is
invoked via wrangle.main() (bare `scourgify`); see cli.py and CLAUDE.md."""
import os, csv, time, collections

from scourgify import ui                    # first: gives the friendly error if rich is missing
from scourgify.ui import console
from rich import box
from rich.table import Table

from scourgify import common, wrangle, classify, staleness, select, promote
from scourgify.common import library, db_path, ro_connect, custom_column_id, calibre_open

COLS = ["#fandoms", "#characters", "#relationships", "#genres", "#status", "#updated", "#wrangled"]
ENGINE_KEYS = {"claude": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
               "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "mistral": ("MISTRAL_API_KEY",)}


# ---------------- status header ----------------
def snapshot():
    try:
        con = ro_connect()
        books = con.execute("SELECT count(*) FROM books").fetchone()[0]
        missing = [c for c in COLS if custom_column_id(con, c) is None]
        # new/changed since the last classify-apply — same select.changed() the classify stage uses
        changed = len(select.changed(con)) if "#updated" not in missing and "#wrangled" not in missing else None
        con.close()
    except Exception as e:
        raise SystemExit(f"can't read {db_path()} — is CALIBRE_LIBRARY correct? ({e})")
    pending = to_stamp = 0
    if os.path.exists(classify.PROP):
        for r in csv.DictReader(open(classify.PROP)):
            if r.get("added_tags", "").strip(): pending += 1     # books that will gain tags
            else: to_stamp += 1                                  # no-match books awaiting a stamp (so they aren't re-sent)
    # cheap file-based signals of unfinished work, surfaced as menu hints
    candidates = 0
    if os.path.exists(classify.RANK):
        try: candidates = len(promote.candidates())          # new-tag candidates not yet adjudicated
        except SystemExit: candidates = 0
    verdicts_pending = os.path.exists(promote.REVIEW)         # adjudicated promote verdicts awaiting apply
    rejects = 0
    if os.path.exists(common.REJECTS):
        rejects = sum(1 for r in csv.DictReader(open(common.REJECTS))
                      if r.get("stage") == "wrangle" and r.get("class") == "auto")
    backfill_n = 0                                            # actual books that would gain a tag — clears once backfilled,
    if os.path.exists(promote.LEDGER):                       # unlike a "ledger has promotions" flag, which never clears
        try: backfill_n = len(promote.backfill_plan()[0])
        except Exception: backfill_n = 0
    return {"books": books, "missing": missing, "changed": changed,
            "pending": pending, "to_stamp": to_stamp, "calibre": calibre_open(),
            "candidates": candidates, "verdicts_pending": verdicts_pending,
            "rejects": rejects, "backfill": backfill_n,
            "setup_needed": bool(missing) or not os.path.exists(os.path.join(os.getcwd(), "config.toml"))}


def header(info):
    g = Table.grid(padding=(0, 2))
    g.add_column(style="bold"); g.add_column()
    g.add_row("library", f"{library()}  ·  {info['books']:,} books")
    g.add_row("columns", "[green]all present ✓[/]" if not info["missing"]
              else f"[yellow]missing: {', '.join(info['missing'])}[/]  → setup will fix this")
    if info["changed"] is not None:
        g.add_row("changes", f"[cyan]{info['changed']} books new/changed since the last classify[/]"
                  if info["changed"] else "[green]library up to date ✓[/]")
    g.add_row("proposal", f"[cyan]{info['pending']} books queued to apply[/]  → the review step"
              if info["pending"] else "[dim]none pending[/]")
    if info["calibre"]:
        g.add_row("calibre", "[bold red]RUNNING[/] — reads work; the write steps will refuse until you close it")
    ui.panel(g, title="[bold]scourgify[/]")


# ---------------- lifecycle stages ----------------
def stage_setup():
    wrangle.setup(wrangle.load_config())


def stage_wrangle():
    cfg = wrangle.load_config(); maps = wrangle.load_maps(cfg)
    n = wrangle.apply_changes(cfg, maps, do_write=False, cli_hint=False)   # dry run: report + SAFETY
    if not n:
        ui.say("nothing to normalize ✓", "green"); return
    ui.say("(per-value detail any time: scourgify audit)", "dim")
    choice = ui.menu(f"apply to {n} books? (Calibre closed; auto-backup)", [
        ("a", "apply all", "write every book's normalizations in one pass"),
        ("r", "review 1-by-1", "walk each book's unique changes; untick to reject (mass folds auto-apply)"),
        ("s", "skip", "leave the library unchanged"),
    ], default="a")
    if choice == "s":
        ui.say("(skipped — nothing written)", "dim"); return
    wrangle.apply_changes(cfg, maps, do_write=True, detail=False, step=choice == "r")
    ui.say("done ✓", "green")


def stage_staleness():
    label, rows = staleness.compute()
    if not rows:
        ui.say("all #status values already consistent ✓", "green"); return
    t = Table(box=box.SIMPLE, title=f"{label} re-derivations")
    t.add_column("transition"); t.add_column("books", justify="right")
    for k, c in collections.Counter(f"{o} → {n}" for _, o, n, _ in rows).most_common():
        t.add_row(k, str(c))
    console.print(t)
    ui.say("examples: " + ", ".join(f"#{b} {o}→{n} ({yrs:.1f}y)" for b, o, n, yrs in rows[:5]), "dim")
    if ui.confirm(f"re-derive {label} for {len(rows)} books? (Calibre closed; auto-backup)", default=True):
        staleness.write(label, rows)
        ui.say("done ✓", "green")
    else:
        ui.say("(skipped — nothing written)", "dim")


def _engines():
    """[(name, usable, hint)] — apple needs the afm binary or a swift toolchain; cloud engines need a key."""
    import shutil
    out = []
    for e in ("apple", "claude", "openai", "gemini", "mistral"):
        if e == "apple":
            ok = os.path.exists(os.path.join(common.HERE, "afm")) or bool(shutil.which("swift"))
            hint = "free, on-device" if ok else "needs the afm binary or a swift toolchain"
        else:
            ok = any(os.environ.get(k) for k in ENGINE_KEYS[e])
            hint = "key set ✓" if ok else "no API key in env"
        out.append((e, ok, hint))
    return out


def stage_classify():
    con = ro_connect(); ch = select.changed(con); con.close()
    if not ch:
        ui.say("no new or changed books since the last classify — nothing to tag ✓", "green")
        ui.say("(a specific redo any time: scourgify classify --last 30, or --since 2026-06-01)", "dim")
        return
    why = collections.Counter(ch.values())
    ui.say(f"[cyan]{len(ch)}[/] books to tag: " + ", ".join(f"{n} {r}" for r, n in why.most_common()))
    a = classify.build_parser().parse_args([])    # normalize() runs ONCE below, AFTER the engine is chosen —
                                                  # normalizing now (engine still defaults to apple) would clamp workers to 1
    a.incremental = a.yes = True                  # the wizard's own confirm below replaces the CLI spend gate
    a.text_fallback = True                        # thin descriptions sample the book text instead of being dropped
    targets, _, _ = classify.gather(a)
    if not targets:
        ui.say("no candidates with usable text — nothing to send ✓", "green"); return
    while True:                                   # engine choice; 'compare' loops back after the bake-off table
        engs = _engines()                          # [(name, usable, hint)] — computed once, reused below
        hints = {e: h for e, _, h in engs}
        usable = {e: ok for e, ok, _ in engs}
        opts = []
        for i, (e, ok, hint) in enumerate(engs, 1):
            cost = classify.est_cost(len(targets), e)
            opts.append((str(i), e, f"{hint}  ·  {'free' if not cost else f'~${cost:.2f}'} for {len(targets)} books"))
        n_sample = min(5, len(targets))
        opts.append(("c", "compare", f"try {n_sample} sample books on every usable engine first"))
        k = ui.menu("engine", opts, default="1")
        if k != "c":
            a.engine = dict((key, lbl) for key, lbl, _ in opts)[k]
            if not usable.get(a.engine):
                ui.error(f"{a.engine} isn't usable here — {hints[a.engine]}")
                continue
            break
        engines = [e for e, ok, _ in engs if ok]
        ui.say(f"comparing: {n_sample} books × {', '.join(engines)} (sequential — a minute or two)…", "dim")
        res = classify.bakeoff(a, targets, engines, n=n_sample)
        con = ro_connect(); titles = {b: t for b, t in con.execute("SELECT id, title FROM books")}; con.close()
        t = Table(box=box.SIMPLE, title="engine comparison — vocab tags (+new candidates dimmed)")
        t.add_column("book", max_width=32)
        for e in engines: t.add_column(e, overflow="fold")
        for b, per in res.items():
            row = [str(titles.get(b, b))[:32]]
            for e in engines:
                vt, nt, err = per.get(e, ([], [], "—"))
                row.append(f"[red]{err}[/]" if err else ("; ".join(vt) or "[dim]none[/]")
                           + (f"\n[dim]+ {'; '.join(nt)}[/]" if nt else ""))
            t.add_row(*row)
        console.print(t)
    if a.engine != "apple" and not ui.confirm(
            f"send {len(targets)} books to the {a.engine} API (~${classify.est_cost(len(targets), a.engine):.2f})?"):
        ui.say("(skipped — nothing sent)", "dim"); return
    a = classify.normalize(a)                     # apple → workers=1
    classify.classify_run(a)


def stage_review():
    if not os.path.exists(classify.PROP):
        ui.say("no pending proposal — nothing to review ✓", "green"); return
    vintage = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(classify.PROP)))
    rows = list(csv.DictReader(open(classify.PROP)))
    tagged = [r for r in rows if r.get("added_tags", "").strip()]
    if not rows:
        ui.say("proposal is empty — nothing to apply ✓", "green"); return
    if not tagged:                                    # every book was classified but matched no new vocab tags
        ui.say(f"{len(rows)} books were classified but got no new vocab tags this run.", "yellow")
        ui.say("apply to STAMP them as processed — else they're re-sent to the LLM every run.", "dim")
        choice = ui.menu("proposal", [
            ("a", "apply", f"stamp {len(rows)} processed books so they aren't re-classified (no tags to add)"),
            ("d", "discard", "set aside — books stay unstamped and WILL be re-classified next run"),
        ], default="a")
        if choice == "a":
            classify.apply_proposal(); ui.say("done ✓", "green")
        else:
            arch = classify.PROP.replace(".csv", f"_discarded_{time.strftime('%Y%m%d-%H%M%S')}.csv")
            os.rename(classify.PROP, arch); ui.say(f"set aside -> {os.path.basename(arch)}", "dim")
        return
    cnt = collections.Counter(t for r in tagged for t in r["added_tags"].split("; ") if t.strip())
    t = Table(box=box.SIMPLE, title=f"proposal from {vintage} — {len(tagged)} of {len(rows)} books get tags")
    t.add_column("vocab tag"); t.add_column("books", justify="right")
    for tag, c in cnt.most_common(15): t.add_row(tag, str(c))
    console.print(t)
    if os.path.exists(classify.RANK):
        r = Table(box=box.SIMPLE, title="top new-tag candidates (promote into overrides/classify_vocab.txt)")
        r.add_column("count", justify="right", style="cyan"); r.add_column("proposed tag")
        for row in list(csv.DictReader(open(classify.RANK)))[:15]: r.add_row(row["count"], row["proposed_tag"])
        console.print(r)
    if os.path.exists(classify.FAIL):
        n = sum(1 for _ in csv.DictReader(open(classify.FAIL)))
        if n: ui.say(f"⚠ {n} books failed classification — see {classify.FAIL} (recover with --engine apple)", "yellow")
    ui.say(f"full proposal: {classify.PROP}", "dim")
    choice = ui.menu("proposal", [
        ("a", "apply", f"write tags to {len(tagged)} books + stamp all {len(rows)} processed (Calibre closed; auto-backup)"),
        ("r", "review 1-by-1", "walk each book's tags; untick to reject an AI-guessed tag before it's written"),
        ("k", "keep", "leave it pending — hand-review the CSV first; the wizard offers it again next run"),
        ("d", "discard", "set it aside without applying (archived as *_discarded_*.csv, nothing written)"),
    ], default="a")
    if choice == "a":
        classify.apply_proposal()
        ui.say("done ✓", "green")
    elif choice == "r":
        classify.apply_proposal_step()
        ui.say("done ✓", "green")
    elif choice == "d":
        arch = classify.PROP.replace(".csv", f"_discarded_{time.strftime('%Y%m%d-%H%M%S')}.csv")
        os.rename(classify.PROP, arch)
        ui.say(f"set aside -> {os.path.basename(arch)}", "dim")
    else:
        ui.say("(kept pending)", "dim")


def _promote_review_menu():
    """Show the adjudicated verdicts and apply / keep / discard them (shared: fresh run + pending review)."""
    rows = list(csv.DictReader(open(promote.REVIEW)))
    by = collections.defaultdict(list)
    for r in rows: by[r["verdict"]].append(r)
    for v, col in (("promote", "green"), ("alias", "cyan"), ("reject", "dim")):
        rs = by.get(v, [])
        if not rs: continue
        t = Table(box=box.SIMPLE, title=f"[{col}]{v}[/] — {len(rs)}")
        t.add_column("candidate"); t.add_column("→ target" if v == "alias" else "reason", overflow="fold")
        for r in rs[:20]:
            mark = " [yellow]⚠[/]" if r.get("contested") == "True" else ""
            t.add_row(r["tag"] + mark, r["target"] if v == "alias" else r.get("reason", "")[:80])
        if len(rs) > 20: t.add_row("[dim]…[/]", f"[dim]+{len(rs) - 20} more[/]")
        console.print(t)
    ui.say(f"full verdicts (edit before applying if you like): {promote.REVIEW}", "dim")
    npro, nal = len(by.get("promote", [])), len(by.get("alias", []))
    choice = ui.menu("verdicts", [
        ("a", "apply", f"promote {npro} to the vocab, fold {nal} aliases (writes overrides/)"),
        ("k", "keep", "leave the review file to hand-edit; the wizard offers it again next run"),
        ("d", "discard", "set aside without applying (archived; nothing written)"),
    ], default="a" if (npro or nal) else "d")
    if choice == "a":
        promote.apply_decisions(); ui.say("done ✓  (run the backfill step to tag the source books)", "green")
    elif choice == "d":
        arch = promote.REVIEW.replace(".csv", f"_discarded_{time.strftime('%Y%m%d-%H%M%S')}.csv")
        os.rename(promote.REVIEW, arch); ui.say(f"set aside -> {os.path.basename(arch)}", "dim")
    else:
        ui.say("(kept pending)", "dim")


def stage_promote():
    if os.path.exists(promote.REVIEW):            # verdicts already adjudicated — apply them, don't re-spend the API
        ui.say("a previously-adjudicated review is pending — apply it, or discard to re-adjudicate.", "dim")
        _promote_review_menu(); return
    if not os.path.exists(classify.RANK):
        ui.say("no new-tag candidates yet — run classify first ✓", "green"); return
    cands = promote.candidates()
    if not cands:
        ui.say("no undecided tag candidates to adjudicate ✓", "green"); return
    ui.say(f"[cyan]{len(cands)}[/] new-tag candidates to weigh against the master tag list "
           "(promote / alias / reject)")
    a = promote.normalize(promote.build_parser().parse_args([]))
    a.yes = True                                  # the wizard's confirm below replaces the CLI guards
    engs = _engines()                             # computed once, reused for opts + the error hint
    hints = {e: h for e, _, h in engs}
    usable = {e: ok for e, ok, _ in engs}
    opts = [(str(i), e, hint) for i, (e, ok, hint) in enumerate(engs, 1)]
    k = ui.menu("engine", opts, default="2")      # default claude; skip apple (too weak for this judgement)
    a.engine = dict((key, lbl) for key, lbl, _ in opts)[k]
    if not usable.get(a.engine):
        ui.error(f"{a.engine} isn't usable here — {hints[a.engine]}"); return
    if a.engine == "apple":
        ui.say("note: on-device apple is weak at this reasoning — a cloud engine gives far better verdicts.", "yellow")
    if a.engine != "apple" and not ui.confirm(f"send {len(cands)} candidates to the {a.engine} API?"):
        ui.say("(skipped)", "dim"); return
    promote.run(a)
    _promote_review_menu()


def stage_backfill():
    chg, adds = promote.backfill_plan()
    if not chg:
        ui.say("no promoted tags to backfill — source books are already up to date ✓", "green")
        ui.say("(backfill applies vocab-promoted tags to the books that first suggested them)", "dim")
        return
    total = sum(len(v) for v in adds.values())
    con = ro_connect(); titles = {b: t for b, t in con.execute("SELECT id, title FROM books")}; con.close()
    t = Table(box=box.SIMPLE, title=f"backfill — {len(chg)} books gain {total} promoted/aliased tags")
    t.add_column("book"); t.add_column("adds")
    for b in list(adds)[:12]:
        t.add_row(f"#{b} {str(titles.get(b, ''))[:36]}", ", ".join(sorted(adds[b])))
    if len(adds) > 12: t.add_row("[dim]…[/]", f"[dim]+{len(adds) - 12} more books[/]")
    console.print(t)
    if ui.confirm(f"apply promoted tags to {len(chg)} source books? (Calibre closed; auto-backup)", default=True):
        common.run_writer([{"op": "set_field", "field": "tags", "values": chg}])
        ui.say("done ✓", "green")
    else:
        ui.say("(skipped — nothing written)", "dim")


def stage_overrides():
    if not os.path.exists(common.REJECTS):
        ui.say("no rejected changes logged — nothing to convert ✓", "green")
        ui.say("(reject deterministic changes in `apply --step` to feed this)", "dim")
        return
    wrangle.build_overrides(do_apply=False)                    # dry-run preview (grouped by target file)
    if ui.confirm("write these override lines to overrides/?", default=False):
        wrangle.build_overrides(do_apply=True)
        ui.say("done ✓", "green")
    else:
        ui.say("(previewed only — nothing written)", "dim")


# key, name, one-line description, stage fn, in-the-guided-workflow?
TASKS = [
    ("1", "wrangle",   "normalize raw tags/fandoms/characters/genres — deterministic cleanup first, so "
                       "junk tags don't hide books from the classifier", stage_wrangle, True),
    ("2", "staleness", "re-derive #status from #updated age (free, no API)", stage_staleness, True),
    ("3", "classify",  "AI content tagging — only books new/changed since the last run", stage_classify, True),
    ("4", "review",    "inspect the pending proposal, then apply it to the library", stage_review, True),
    ("5", "promote",   "adjudicate new-tag candidates against the master list — promote / alias / reject",
                       stage_promote, True),
    ("6", "backfill",  "apply vocab-promoted tags to the books that first suggested them (deterministic)",
                       stage_backfill, True),
    ("7", "overrides", "turn --step-rejected deterministic changes into personal override rules",
                       stage_overrides, False),
]
WORKFLOW = [(name, why, fn) for k, name, why, fn, wf in TASKS if wf]
_FN = {name: fn for k, name, why, fn, wf in TASKS}
_NAME_BY_KEY = {k: name for k, name, why, fn, wf in TASKS}
_WF_NAMES = [name for name, why, fn in WORKFLOW]
# natural successor for a standalone task = the next stage in the guided workflow (overrides has none)
NEXT = {_WF_NAMES[i]: _WF_NAMES[i + 1] for i in range(len(_WF_NAMES) - 1)}


# ---------------- the guided run ----------------
def _stage_guard(fn):
    """Run one stage, absorbing its guardrail SystemExit / Ctrl-C so the menu survives. -> ok?"""
    try:
        fn(); return True
    except SystemExit as e:                    # guardrails/aborts skip the stage, not the session
        if str(e): ui.error(str(e))
    except (KeyboardInterrupt, EOFError):
        ui.say("\n(cancelled — nothing written beyond what was already confirmed)", "dim")
    return False


def _run_stage(name):
    console.rule(f"[bold]{name}[/]", style="cyan")
    _stage_guard(_FN[name])


def run_task(key):
    """Run the chosen standalone task, then offer its natural workflow successor, one step at a time,
    so a task you jumped into can flow onward like the guided run instead of dead-ending at the menu."""
    _run_stage(_NAME_BY_KEY[key])
    nxt = NEXT.get(_NAME_BY_KEY[key])
    while nxt and ui.confirm(f"continue to the natural next step — [bold]{nxt}[/]?", default=True):
        _run_stage(nxt)
        nxt = NEXT.get(nxt)


def run_workflow():
    """The guided lifecycle: every workflow stage in order, each dry-running + asking before it writes."""
    for i, (name, why, fn) in enumerate(WORKFLOW, 1):
        console.rule(f"[bold]step {i}/{len(WORKFLOW)} · {name}[/]", style="cyan")
        ui.say(why, "dim")
        if not _stage_guard(fn):
            if not ui.confirm("continue with the remaining steps?", default=True): return
    console.rule(style="green")
    ui.say("maintenance run complete ✓", "bold green")


def _task_hint(name, info):
    """The cyan 'pending work' marker for a task, from the file-based snapshot signals."""
    if name == "classify": return f"{info['changed']} new/changed" if info.get("changed") else ""
    if name == "review":
        if info["pending"]: return f"{info['pending']} books to apply"
        return f"{info['to_stamp']} to stamp" if info.get("to_stamp") else ""
    if name == "promote":
        bits = [f"{info['candidates']} candidates" if info.get("candidates") else "",
                "verdicts ready to apply" if info.get("verdicts_pending") else ""]
        return " · ".join(b for b in bits if b)
    if name == "backfill": return f"{info['backfill']} books to backfill" if info.get("backfill") else ""
    if name == "overrides":return f"{info['rejects']} rejects to convert" if info.get("rejects") else ""
    return ""


def landing_menu(info):
    """Ask what to do: the whole guided run, or a single task. Pending work is flagged inline."""
    opts = [("w", "full maintenance run", "the guided lifecycle end to end: " +
             " → ".join(name for name, _, _ in WORKFLOW))]
    for k, name, why, fn, wf in TASKS:
        hint = _task_hint(name, info)
        short = why.split(" — ")[0].split(",")[0][:60]        # keep the menu row tight
        opts.append((k, name, (f"[cyan]● {hint}[/]  " if hint else "") + f"[dim]{short}[/]"))
    opts.append(("q", "quit", "leave the wizard"))
    return ui.menu("what would you like to do?", opts, default="w")


def _run():
    library()                                 # fail fast with the clear CALIBRE_LIBRARY message
    if not ui.interactive():
        raise SystemExit("the wizard needs an interactive terminal — use the subcommands instead "
                         "(scourgify --help).")
    ui.clear()
    info = snapshot()
    if info["setup_needed"]:
        header(info)
        ui.say("this library isn't fully set up yet — the tools need their columns and config.toml.", "yellow")
        if not ui.confirm("run setup now?", default=True):
            ui.say("(the rest of the lifecycle needs setup — exiting)", "dim"); return
        if not _stage_guard(stage_setup): return
        info = snapshot()
    while True:
        header(info)
        choice = landing_menu(info)
        if choice == "q":
            break
        elif choice == "w":
            run_workflow()
        else:
            run_task(choice)                  # single task + offer of the natural next step(s)
        info = snapshot()                     # refresh so the next menu reflects what just changed
    console.print(); ui.say("done — run `scourgify` any time to pick up where you left off.", "dim")


def run():
    try:
        _run()
    except (KeyboardInterrupt, EOFError):     # Ctrl+C / Ctrl+D anywhere = quit cleanly
        console.print()


if __name__ == "__main__":
    run()
