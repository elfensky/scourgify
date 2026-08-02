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
import os, time, collections

from scourgify import ui                    # first: gives the friendly error if rich is missing
from scourgify.ui import console
from rich import box
from rich.table import Table

from scourgify import artifacts, common, engines, wrangle, classify, staleness, select, promote, overrides
from scourgify import setup as setup_mod
from scourgify.common import library, db_path, load_config, ro_connect, custom_column_id, calibre_open

LAST_DEFAULT  = 30      # wizard default for the 'most recent N' redo (matches the documented --last 30)
BATCH_DEFAULT = 100     # wizard chunk size for the never-classified backlog; below SPEND_GATE
                        # deliberately NOT equal to it, so the spend confirm still fires on a repeat sweep
COLS = ["#fandoms", "#characters", "#relationships", "#genres", "#status", "#updated", "#wrangled"]
ENGINE_KEYS = engines.ENGINE_ENV               # single source of truth (defined in engines); never disagree


# ---------------- status header ----------------
def _proposal_counts(rows: list) -> tuple[int, int]:
    """(pending, to_stamp) from artifacts.read_proposal rows (added_tags is a list): books that will
    gain tags vs no-match books awaiting only a stamp (so they aren't re-sent forever). Pure — see tests."""
    pending = sum(1 for r in rows if r.get("added_tags"))
    return pending, len(rows) - pending


def snapshot():
    try:
        con = ro_connect()
        books = common.book_count(con)
        missing = [c for c in COLS if custom_column_id(con, c) is None]
        # new/changed since the last classify-apply — same select.changed() the classify stage uses
        changed = len(select.changed(con)) if "#updated" not in missing and "#wrangled" not in missing else None
        # the largest single piece of outstanding work in most libraries, and it used to be
        # invisible here — the header cheerfully said "up to date" with thousands never attempted.
        # text_fallback=True because the wizard always samples book text, so the count matches the
        # scope the classify stage will actually resolve. ~0.03s on a 7,949-book library.
        try: unclassified = len(select.pick(con, "unclassified", seen=artifacts.classified_ids(),
                                            text_fallback=True))
        except Exception: unclassified = 0
        con.close()
    except Exception as e:
        raise SystemExit(f"can't read {db_path()} — is CALIBRE_LIBRARY correct? ({e})")
    pending, to_stamp = _proposal_counts(artifacts.read_proposal())
    # cheap file-based signals of unfinished work, surfaced as menu hints
    candidates = 0
    if os.path.exists(artifacts.rank()):
        try: candidates = len(promote.candidates())          # new-tag candidates not yet adjudicated
        except SystemExit: candidates = 0
    verdicts_pending = os.path.exists(artifacts.review())       # adjudicated promote verdicts awaiting apply
    rejects = sum(1 for r in artifacts.read_rows(common.rejects_path())
                  if r.get("stage") == "wrangle" and r.get("class") == "auto")
    backfill_n = 0                                            # actual books that would gain a tag — clears once backfilled,
    if os.path.exists(artifacts.ledger()):                     # unlike a "ledger has promotions" flag, which never clears
        try: backfill_n = len(promote.backfill_plan()[0])
        except Exception: backfill_n = 0
    return {"books": books, "missing": missing, "changed": changed, "unclassified": unclassified,
            "pending": pending, "to_stamp": to_stamp, "calibre": calibre_open(),
            "candidates": candidates, "verdicts_pending": verdicts_pending,
            "rejects": rejects, "backfill": backfill_n, "backups": common.backups_size(),
            "setup_needed": bool(missing) or not os.path.exists(os.path.join(common.user_dir(), "config.toml"))}


def header(info):
    g = Table.grid(padding=(0, 2))
    g.add_column(style="bold"); g.add_column()
    g.add_row("library", f"{library()}  ·  {info['books']:,} books")
    g.add_row("columns", "[green]all present ✓[/]" if not info["missing"]
              else f"[yellow]missing: {', '.join(info['missing'])}[/]  → setup will fix this")
    if info["changed"] is not None:
        bits = []
        if info["changed"]: bits.append(f"[cyan]{info['changed']} new/changed since the last classify[/]")
        if info.get("unclassified"):
            bits.append(f"[cyan]{info['unclassified']:,} never classified[/]  → the classify step, a chunk at a time")
        g.add_row("classify", "  ·  ".join(bits) if bits else "[green]every book classified ✓[/]")
    g.add_row("proposal", f"[cyan]{info['pending']} books queued to apply[/]  → the review step"
              if info["pending"] else "[dim]none pending[/]")
    n, b = info["backups"]
    if n:
        sz = f"{b / 1e9:.1f} GB" if b >= 1e9 else f"{b / 1e6:.0f} MB"
        loc = f"[dim]{os.path.join(common.user_dir(), 'data', 'backups')}[/]"
        g.add_row("backups", f"[yellow]{n} snapshots · {sz}[/] → getting large; delete old ones to reclaim space  {loc}"
                  if b > common.BACKUP_WARN else f"{n} snapshots · {sz}  {loc}")
    if info["calibre"]:
        g.add_row("calibre", "[bold red]RUNNING[/] — reads work; the write steps will refuse until you close it")
    ui.panel(g, title="[bold]scourgify[/]")


# ---------------- lifecycle stages ----------------
def stage_setup():
    setup_mod.setup(load_config())


def stage_wrangle():
    cfg = load_config()
    p = wrangle.plan(cfg, wrangle.load_maps(cfg))   # ONE compute: the preview, step review, and write all read it
    p.preview()                                     # dry-run report (mass folds + unique changes + SAFETY)
    p.guard()                                       # guardrail SystemExit -> _stage_guard skips the stage
    if not p.n_books:
        ui.say("nothing to normalize ✓", "green"); return
    ui.say("(per-value detail any time: scourgify audit)", "dim")
    choice = ui.menu(f"apply to {p.n_books} books? (Calibre closed; auto-backup)", [
        ("1", "apply", "apply all", "write every book's normalizations in one pass"),
        ("2", "step", "review 1-by-1", "walk each book's unique changes; untick to reject (mass folds auto-apply)"),
        ("3", "last", "most recent N books", "write only the newest N — try a rule change on a few books first"),
        ("4", "skip", "skip", "leave the library unchanged"),
    ], default="apply")
    if choice == "skip":
        ui.say("(skipped — nothing written)", "dim"); return
    if choice == "last":
        con = ro_connect(); total = common.book_count(con)
        n = ui.ask_int(f"how many of the most recent books?  {total:,} in the library",
                       LAST_DEFAULT, lo=1, hi=total)
        p.restrict(select.pick(con, "last", n=n)); con.close()   # narrows the WRITE set, not the read
        if not p.n_books:
            ui.say("none of those books need changes ✓", "green"); return
        ui.say(f"scoped to {p.n_books} of the newest {n} books", "dim")
    if choice == "step":
        p.step()
    p.write()
    ui.say("done ✓", "green")


def stage_staleness():
    label, rows = staleness.compute()
    if not rows:
        ui.say("all #status values already consistent ✓", "green"); return
    staleness.show(label, rows)                     # the ONE renderer — same output as `scourgify staleness`
    choice = ui.menu(f"re-derive {label} for {len(rows)} books? (Calibre closed; auto-backup)", [
        ("1", "apply", "apply all", "re-derive #status for every book listed above"),
        ("2", "step", "review 1-by-1", "walk each book; untick one to leave its #status alone"),
        ("3", "last", "most recent N books", "only the newest N of them"),
        ("4", "skip", "skip", "leave #status unchanged"),
    ], default="apply")
    if choice == "skip":
        ui.say("(skipped — nothing written)", "dim"); return
    if choice == "step":
        rows = staleness.step(label, rows)        # the SAME function `staleness --apply --step` calls
        if not rows:
            ui.say("(nothing decided — nothing written)", "dim"); return
        ui.say(f"{len(rows)} book(s) accepted", "dim")
    if choice == "last":
        con = ro_connect(); total = common.book_count(con)
        n = ui.ask_int(f"how many of the most recent books?  {total:,} in the library",
                       LAST_DEFAULT, lo=1, hi=total)
        want = set(select.pick(con, "last", n=n)); con.close()
        rows = [r for r in rows if r[0] in want]
        if not rows:
            ui.say("none of those books need a status change ✓", "green"); return
        ui.say(f"scoped to {len(rows)} of the newest {n} books", "dim")
    staleness.write(label, rows)
    ui.say("done ✓", "green")


def _engines(env=None):
    """[(name, usable, hint)] for the engine menu — derived from engines.ENGINES + usable_engines()
    + TRAITS, so a newly registered engine shows up here automatically instead of silently missing.
    `env` passes through to usable_engines (tests inject a dict)."""
    ok = set(engines.usable_engines(env))
    return [(e, e in ok, engines.trait(e, "hint" if e in ok else "unusable")) for e in engines.ENGINES]


def _default_engine_id(opts: list, judge: bool = False, usable=None) -> str:
    """PURE half of the menu default: the first option normally; for judge work (promote's
    adversarial refereeing) the first judge-capable engine. Both prefer a USABLE engine when
    `usable` is given — defaulting to one with no API key turns ⏎ into an error the picker has
    to reject and re-ask. `usable` also excludes the non-engine `extra` rows, which is right."""
    ok = (lambda ident: usable is None or ident in usable)
    fallback = next((i for _, i, _, _ in opts if ok(i)), opts[0][1])
    if not judge: return fallback
    return next((i for _, i, _, _ in opts if engines.trait(i, "judge") and ok(i)), fallback)


def _ask_engine(n_todo: int | None = None, judge: bool = False, extra: tuple = ()):
    """The ONE engine-menu drive loop (classify + promote share it): numbered engines (with a
    per-engine cost column when n_todo is given), `extra` rows appended verbatim (their key is
    returned as-is), re-ask on an unusable choice. -> engine name, an extra key, or None when
    no engine is usable at all."""
    while True:
        engs = _engines()
        if not any(ok for _, ok, _ in engs):
            ui.error("no engine is usable — set an API key, or install the afm binary / a swift toolchain.")
            return None
        opts = (_engine_options(engs, n_todo) if n_todo is not None
                else [(str(i), e, e, h) for i, (e, _, h) in enumerate(engs, 1)])
        # extras continue the engine numbering but keep their own symbolic id, so the caller never
        # has to know how many engines exist (renumbering them by position used to return a digit
        # as the engine name -> KeyError, which _stage_guard does not absorb: session over)
        opts += [(str(len(engs) + i), ident, lbl, hint) for i, (ident, lbl, hint) in enumerate(extra, 1)]
        name = ui.menu("engine", opts, default=_default_engine_id(opts, judge, {e for e, ok, _ in engs if ok}))
        if name in {ident for ident, _, _ in extra}: return name
        if not {e: ok for e, ok, _ in engs}[name]:
            ui.error(f"{name} isn't usable here — {dict((e, h) for e, _, h in engs)[name]}"); continue
        return name


def _scope_options(ch: dict, total: int, outstanding: int = 0) -> tuple[list, str]:
    """PURE half of the scope menu: (options, default). Every row keeps a FIXED slot whether or
    not it applies — an empty one greys out (id=None) instead of vanishing, so the number a user
    has memorised never comes to mean something else. Slot 1 hiding used to make '2' mean either
    'whole library (real money)' or 'never classified' depending on library state."""
    why = collections.Counter(ch.values())
    opts = [
        ("1", "changed" if ch else None, f"new/changed — {len(ch)} books" if ch else "new/changed — none",
         ("books added or updated since the last classify: "
          + ", ".join(f"{n} {r}" for r, n in why.most_common())) if ch
         else "nothing added or updated since the last classify"),
        ("2", "unclassified" if outstanding else None,
         f"never classified — {outstanding:,} books" if outstanding else "never classified — none",
         "books classify has never attempted; work through them a chunk at a time" if outstanding
         else "every sendable book has been classified at least once"),
        ("3", "last" if total else None, "most recent N books",
         "re-classify a chosen number of the newest books — a targeted redo; these have usually been "
         "classified before, so a paid engine bills them again"),
        ("4", "all", f"whole library — {total:,} books · full pass",
         "re-tag EVERY book regardless of tag count — a paid engine over this many books costs real money"),
        ("5", "skip", "skip", "tag nothing this run (a targeted redo any time: scourgify classify --books / --since DATE)"),
    ]
    return opts, ("changed" if ch else ("unclassified" if outstanding else "all"))


def _engine_options(engs: list, n_todo: int) -> list:
    """PURE half of the engine menu: numbered rows with per-engine cost over the books that will
    actually be billed (the plan's todo set)."""
    opts = []
    for i, (e, ok, hint) in enumerate(engs, 1):
        cost = classify.est_cost(n_todo, e)
        opts.append((str(i), e, e, f"{hint}  ·  {'free' if not cost else f'~${cost:.2f}'} for {n_todo} books"))
    return opts


def stage_classify():
    con = ro_connect(); ch = select.changed(con)
    total = common.book_count(con)
    # the wizard always samples book text, so the outstanding count must be measured the same way
    outstanding = len(select.pick(con, "unclassified", seen=artifacts.classified_ids(), text_fallback=True))
    con.close()
    if not ch:
        ui.say("no new or changed books since the last classify.", "dim")
    opts, default = _scope_options(ch, total, outstanding)
    scope = ui.menu("classify scope", opts, default=default)
    if scope == "skip":
        ui.say("(skipped — nothing tagged)", "dim"); return
    batch = last = 0
    if scope == "last":
        # a targeted redo: --last re-sends its books whether or not they were classified before
        # (an explicitly scoped book bypasses the resume), which is the point of asking for one.
        last = ui.ask_int(f"how many of the most recent books?  {total:,} in the library",
                          LAST_DEFAULT, lo=1, hi=total)
    if scope == "unclassified" and outstanding > BATCH_DEFAULT:
        # the backlog is the whole expensive pass, so ask how much of it to do now. N is a COUNT
        # slicing an identity-keyed set, not a position: the next run resumes at the next N
        # whatever was added, deleted or re-fetched in between.
        batch = ui.ask_int(f"how many books this run?  {outstanding:,} outstanding",
                           BATCH_DEFAULT, lo=1, hi=outstanding)
    # thin descriptions sample the book text instead of being dropped; exactly one scope flag
    # (whole-library reuses select.pick("all")). The plan is resolved ONCE — the cost shown and
    # confirmed below is over the same `todo` set classify_run executes (never a re-gather).
    # NB batch must be set BEFORE plan(): Plan.__init__ is the only reader of it.
    a = classify.default_opts(text_fallback=True, incremental=scope == "changed",
                              unclassified=scope == "unclassified", last=last, batch=batch,
                              **{"all": scope == "all"})
    p = classify.plan(a)                          # owns a COPY of a — steering goes through p.opts
    targets, todo = p.targets, p.todo
    if not targets:
        ui.say("no candidates with usable text — nothing to send ✓", "green"); return
    if not todo:
        ui.say(f"all {len(targets)} candidate(s) already in the pending proposal — the review step applies them ✓",
               "green"); return
    n_sample = min(5, len(targets))
    while True:                                   # engine choice; 'compare' loops back after the bake-off table
        k = _ask_engine(n_todo=len(todo),
                        extra=(("c", "compare", f"try {n_sample} sample books on every usable engine first"),))
        if k is None: return                      # nothing usable — the picker already said why
        if k != "c":
            p.opts.engine = k; break
        usable_engs = engines.usable_engines()         # NB: don't shadow the module-level `engines` import
        ui.say(f"comparing: {n_sample} books × {', '.join(usable_engs)} (sequential — a minute or two)…", "dim")
        res = classify.bakeoff(p.opts, targets, usable_engs, n=n_sample)
        con = ro_connect(); titles = common.titles(con, res); con.close()
        t = Table(box=box.SIMPLE, title="engine comparison — vocab tags (+new candidates dimmed)")
        t.add_column("book", max_width=32)
        for e in usable_engs: t.add_column(e, overflow="fold")
        for b, per in res.items():
            row = [str(titles.get(b, b))[:32]]
            for e in usable_engs:
                vt, nt, err = per.get(e, ([], [], "—"))
                row.append(f"[red]{err}[/]" if err else ("; ".join(vt) or "[dim]none[/]")
                           + (f"\n[dim]+ {'; '.join(nt)}[/]" if nt else ""))
            t.add_row(*row)
        console.print(t)
    if not engines.is_free(p.opts.engine):
        if not ui.confirm(
                f"send {len(todo)} books to the {p.opts.engine} API (~${classify.est_cost(len(todo), p.opts.engine):.2f})?"):
            ui.say("(skipped — nothing sent)", "dim"); return
        p.opts.yes = True                         # this confirm ANSWERS classify's spend gate — never ask twice
    p.run()                                       # the SAME plan that was priced — no second gather


def _proposal_options(n_rows: int, n_tagged: int) -> list:
    """ONE slot layout for the proposal menu whether or not any book got tags. There used to be
    two menus under the same title with different rows, so the same key meant 'review 1-by-1' in
    one and 'discard' in the other. Now 'review 1-by-1' simply greys out (keeping slot 2) when
    there is nothing to walk. Pure — see tests."""
    return [
        ("1", "apply", "apply",
         f"write tags to {n_tagged} books + stamp all {n_rows} processed (Calibre closed; auto-backup)" if n_tagged
         else f"stamp {n_rows} processed books so they aren't re-classified (no tags to add)"),
        ("2", "step" if n_tagged else None, "review 1-by-1",
         "walk each book's tags; untick to reject an AI-guessed tag before it's written" if n_tagged
         else "nothing to walk — no book got tags this run"),
        ("3", "keep", "keep", "leave it pending — hand-review the CSV first; the wizard offers it again next run"),
        ("4", "discard", "discard", "set it aside without applying (archived as *_discarded_*.csv, nothing written)"),
    ]


def _do_proposal(choice: str) -> None:
    """Act on a _proposal_options id — shared by both entries into the menu."""
    if choice == "apply":
        classify.apply_proposal(); ui.say("done ✓", "green")
    elif choice == "step":
        classify.apply_proposal_step(); ui.say("done ✓", "green")
    elif choice == "discard":
        arch = artifacts.archive(artifacts.prop(), "discarded")
        ui.say(f"set aside -> {os.path.basename(arch)}", "dim")
    else:
        ui.say("(kept pending)", "dim")


def stage_review():
    if not os.path.exists(artifacts.prop()):
        ui.say("no pending proposal — nothing to review ✓", "green"); return
    vintage = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(artifacts.prop())))
    rows = artifacts.read_proposal()
    tagged = [r for r in rows if r["added_tags"]]
    if not rows:
        ui.say("proposal is empty — nothing to apply ✓", "green"); return
    if not tagged:                                    # every book was classified but matched no new vocab tags
        ui.say(f"{len(rows)} books were classified but got no new vocab tags this run.", "yellow")
        ui.say("apply to STAMP them as processed — else they're re-sent to the LLM every run.", "dim")
        choice = ui.menu("proposal", _proposal_options(len(rows), 0), default="apply")
        _do_proposal(choice)
        return
    cnt = collections.Counter(t for r in tagged for t in r["added_tags"])
    t = Table(box=box.SIMPLE, title=f"proposal from {vintage} — {len(tagged)} of {len(rows)} books get tags")
    t.add_column("vocab tag"); t.add_column("books", justify="right")
    for tag, c in cnt.most_common(15): t.add_row(tag, str(c))
    console.print(t)
    if os.path.exists(artifacts.rank()):
        r = Table(box=box.SIMPLE, title="top new-tag candidates (promote into overrides/classify_vocab.txt)")
        r.add_column("count", justify="right", style="cyan"); r.add_column("proposed tag")
        for row in artifacts.read_ranked()[:15]: r.add_row(str(row["count"]), row["proposed_tag"])
        console.print(r)
    n_failed = len(artifacts.read_rows(artifacts.fail()))
    if n_failed:
        ui.say(f"⚠ {n_failed} books failed classification — see {artifacts.fail()} (recover with --engine apple)", "yellow")
    ui.say(f"full proposal: {artifacts.prop()}", "dim")
    _do_proposal(ui.menu("proposal", _proposal_options(len(rows), len(tagged)), default="apply"))


def _promote_review_menu():
    """Show the adjudicated verdicts and apply / keep / discard them (shared: fresh run + pending review)."""
    rows = artifacts.read_rows(artifacts.review())
    by = collections.defaultdict(list)
    for r in rows: by[r["verdict"]].append(r)
    for v, col in (("promote", "green"), ("alias", "cyan"), ("reject", "dim"), ("error", "red")):
        rs = by.get(v, [])
        if not rs: continue
        t = Table(box=box.SIMPLE, title=f"[{col}]{v}[/] — {len(rs)}")
        t.add_column("candidate"); t.add_column("→ target" if v == "alias" else "reason", overflow="fold")
        for r in rs[:20]:
            mark = " [yellow]⚠[/]" if r.get("contested") == "True" else ""
            t.add_row(r["tag"] + mark, r["target"] if v == "alias" else r.get("reason", "")[:80])
        if len(rs) > 20: t.add_row("[dim]…[/]", f"[dim]+{len(rs) - 20} more[/]")
        console.print(t)
    ui.say(f"full verdicts (edit before applying if you like): {artifacts.review()}", "dim")
    npro, nal = len(by.get("promote", [])), len(by.get("alias", []))
    choice = ui.menu("verdicts", [
        ("1", "apply", "apply all", f"promote {npro} to the vocab, fold {nal} aliases (writes overrides/)"),
        ("2", "step", "review 1-by-1", "walk each candidate; untick a verdict you disagree with "
                                       "(it stays undecided and is offered again)"),
        ("3", "keep", "keep", "leave the review file to hand-edit; the wizard offers it again next run"),
        ("4", "discard", "discard", "set aside without applying (archived; nothing written)"),
    ], default="apply" if (npro or nal) else "discard")
    if choice in ("apply", "step"):
        res = promote.apply_decisions_step() if choice == "step" else promote.apply_decisions()
        ui.say("done ✓  (run the backfill step to tag the source books)", "green")
        if res.get("skipped"):                    # else the candidates hint outlives the apply, unexplained
            ui.say(f"{res['skipped']} candidate(s) could not be decided (engine error / bad alias target) — "
                   "still listed; re-run promote to retry them.", "yellow")
    elif choice == "discard":
        arch = artifacts.archive(artifacts.review(), "discarded")
        ui.say(f"set aside -> {os.path.basename(arch)}", "dim")
    else:
        ui.say("(kept pending)", "dim")


def stage_promote():
    if os.path.exists(artifacts.review()):          # verdicts already adjudicated — apply them, don't re-spend the API
        ui.say("a previously-adjudicated review is pending — apply it, or discard to re-adjudicate.", "dim")
        _promote_review_menu(); return
    if not os.path.exists(artifacts.rank()):
        ui.say("no new-tag candidates yet — run classify first ✓", "green"); return
    cands = promote.candidates()
    if not cands:
        ui.say("no undecided tag candidates to adjudicate ✓", "green"); return
    ui.say(f"[cyan]{len(cands)}[/] new-tag candidates to weigh against the master tag list "
           "(promote / alias / reject)")
    a = promote.default_opts(yes=True)            # yes: the pending-review case was already handled above
    eng = _ask_engine(judge=True)                 # default: first judge-capable engine (apple is not)
    if eng is None: return                        # nothing usable — the picker already said why
    a.engine = eng
    if not engines.trait(a.engine, "judge"):
        ui.say(f"note: {a.engine} is weak at this reasoning — a cloud engine gives far better verdicts.", "yellow")
    if not engines.is_free(a.engine) and not ui.confirm(f"send {len(cands)} candidates to the {a.engine} API?"):
        ui.say("(skipped)", "dim"); return
    promote.run(a)
    _promote_review_menu()


def stage_backfill():
    """Asks; promote.backfill() does the work. The write itself (and its preview, guards and
    auto-backup) lives there, so `promote --backfill` and this stage cannot diverge."""
    def decide(chg, adds):
        con = ro_connect(); titles = common.titles(con); con.close()
        choice = ui.menu(f"backfill {len(chg)} book(s)? (Calibre closed; auto-backup)", [
            ("1", "apply", "apply all", "write the promoted/aliased tags onto every book that proposed them"),
            ("2", "step", "review 1-by-1", "walk each book; untick one to leave it untagged"),
            ("3", "skip", "skip", "leave the books unchanged"),
        ], default="apply")
        if choice == "skip": return None
        return promote.backfill_step(chg, adds, titles) if choice == "step" else chg

    if promote.backfill(decide=decide):
        ui.say("done ✓", "green")
    else:
        ui.say("(backfill applies vocab-promoted tags to the books that first suggested them)", "dim")


def stage_overrides():
    if not os.path.exists(common.rejects_path()):
        ui.say("no rejected changes logged — nothing to convert ✓", "green")
        ui.say("(reject deterministic changes in `apply --step` to feed this)", "dim")
        return
    auto = overrides.build_overrides(do_apply=False) or {}     # dry-run preview (grouped by target file)
    if not auto:
        return
    choice = ui.menu("write these override lines to overrides/?", [
        ("1", "apply", "write all", "append every line above to your overrides/"),
        ("2", "step", "review 1-by-1", "walk each rule; untick one you don't want"),
        ("3", "skip", "skip", "previewed only — nothing written"),
    ], default="skip")
    if choice == "skip":
        ui.say("(previewed only — nothing written)", "dim"); return
    only = None
    if choice == "step":
        only = overrides.step_pick(auto)          # SAME function as `overrides --apply --step`
        if only is None:
            ui.say("(nothing decided — nothing written)", "dim"); return
        ui.say(f"{len(only)} rule(s) accepted", "dim")
    overrides.build_overrides(do_apply=True, only=only)
    ui.say("done ✓", "green")


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


def run_task(name):
    """Run the chosen standalone task, then offer its natural workflow successor, one step at a time,
    so a task you jumped into can flow onward like the guided run instead of dead-ending at the menu."""
    _run_stage(name)
    nxt = NEXT.get(name)
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
    if name == "classify":
        return " · ".join(b for b in (f"{info['changed']} new/changed" if info.get("changed") else "",
                                      f"{info['unclassified']:,} never classified" if info.get("unclassified") else "")
                          if b)
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
    """Ask what to do: the whole guided run, or a single task. Pending work is flagged inline.
    Returns a symbolic id — 'workflow', a task NAME, or 'quit' — never a digit."""
    opts = [("w", "workflow", "full maintenance run", "the guided lifecycle end to end: " +
             " → ".join(name for name, _, _ in WORKFLOW))]
    for k, name, why, fn, wf in TASKS:
        hint = _task_hint(name, info)
        short = why.split(" — ")[0].split(",")[0][:60]        # keep the menu row tight
        opts.append((k, name, name, (f"[cyan]● {hint}[/]  " if hint else "") + f"[dim]{short}[/]"))
    opts.append(("q", "quit", "quit", "leave the wizard"))
    return ui.menu("what would you like to do?", opts, default="workflow")


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
        if choice == "quit":
            break
        elif choice == "workflow":
            run_workflow()
        else:
            run_task(choice)                  # a task NAME + offer of the natural next step(s)
        info = snapshot()                     # refresh so the next menu reflects what just changed
    console.print(); ui.say("done — run `scourgify` any time to pick up where you left off.", "dim")


def run():
    try:
        _run()
    except (KeyboardInterrupt, EOFError):     # Ctrl+C / Ctrl+D anywhere = quit cleanly
        console.print()


if __name__ == "__main__":
    run()
