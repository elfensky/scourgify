#!/usr/bin/env python3
"""One-command guided wizard over the whole toolchain:

    scourgify                    # no arguments — launches this interactive wizard

Menu-driven: setup/health check, audit, normalize (the wrangle passes), staleness,
AI classify with a live dashboard, and proposal review. Every write path previews
first, asks for confirmation, refuses while Calibre is open, and auto-backs up
metadata.db (all writes funnel through common.run_writer)."""
import os, csv, collections

from scourgify import ui                    # first: gives the friendly error if rich is missing
from scourgify.ui import console
from rich import box
from rich.table import Table
from rich.text import Text

from scourgify import common, wrangle, classify, staleness
from scourgify.common import library, db_path, ro_connect, custom_column_id, calibre_open

COLS = ["#fandoms", "#characters", "#relationships", "#genres", "#status", "#updated", "#wrangled"]
ENGINE_KEYS = {"claude": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
               "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}


# ---------------- status header + workflow detection ----------------
def snapshot():
    try:
        con = ro_connect()
        books = con.execute("SELECT count(*) FROM books").fetchone()[0]
        missing = [c for c in COLS if custom_column_id(con, c) is None]
        changed = None                        # books new/updated since the last classify-apply
        if "#updated" not in missing and "#wrangled" not in missing:
            from common import read_custom_column
            upd = read_custom_column(con, "#updated") or {}
            wr = read_custom_column(con, "#wrangled") or {}
            changed = sum(1 for b, v in upd.items() if b not in wr or str(v)[:10] > str(wr[b])[:10])
        con.close()
    except Exception as e:
        raise SystemExit(f"can't read {db_path()} — is CALIBRE_LIBRARY correct? ({e})")
    pending = 0
    if os.path.exists(classify.PROP):
        pending = sum(1 for r in csv.DictReader(open(classify.PROP)) if r.get("added_tags", "").strip())
    return {"books": books, "missing": missing, "changed": changed,
            "pending": pending, "calibre": calibre_open(),
            "setup_needed": bool(missing) or not os.path.exists(os.path.join(common.HERE, "config.toml"))}


def recommend(info):
    """The menu default = the step the library actually needs next."""
    if info["setup_needed"]: return "1"                        # setup first
    if info["pending"]: return "6"                             # a reviewed-but-unapplied proposal is waiting
    if info["changed"]: return "0"                             # new/changed books -> run the loop
    return "2"                                                 # healthy -> audit is the safe look-around


def header(info):
    g = Table.grid(padding=(0, 2))
    g.add_column(style="bold"); g.add_column()
    g.add_row("library", f"{library()}  ·  {info['books']:,} books")
    g.add_row("columns", "[green]all present ✓[/]" if not info["missing"]
              else f"[yellow]missing: {', '.join(info['missing'])}[/]  → run 1 setup")
    if info["changed"] is not None:
        g.add_row("changes", f"[cyan]{info['changed']} books new/updated since the last wrangle[/]  → run 0 maintenance"
                  if info["changed"] else "[green]library up to date ✓[/]")
    g.add_row("proposal", f"[cyan]{info['pending']} books queued to apply[/]  → 6 review"
              if info["pending"] else "[dim]none pending[/]")
    if info["calibre"]:
        g.add_row("calibre", "[bold red]RUNNING[/] — reads work; any write will refuse until you close it")
    ui.panel(g, title="[bold]scourgify[/]")


# ---------------- actions ----------------
def act_setup():
    wrangle.setup(wrangle.load_config())

def act_audit():
    cfg = wrangle.load_config()
    wrangle.audit(cfg, wrangle.load_maps(cfg))

def act_wrangle():
    cfg = wrangle.load_config(); maps = wrangle.load_maps(cfg)
    wrangle.apply_changes(cfg, maps, do_write=False)          # preview + SAFETY (aborts on data loss)
    if ui.confirm("\nwrite these changes now? (Calibre closed; metadata.db is auto-backed-up)"):
        wrangle.apply_changes(cfg, maps, do_write=True)
        ui.say("done ✓", "green")

def act_staleness():
    label, rows = staleness.compute()
    if not rows:
        ui.say("all #status values already consistent ✓", "green"); return
    t = Table(box=box.SIMPLE, title=f"{label} re-derivations")
    t.add_column("transition"); t.add_column("books", justify="right")
    for k, c in collections.Counter(f"{o} → {n}" for _, o, n, _ in rows).most_common():
        t.add_row(k, str(c))
    console.print(t)
    ui.say("examples: " + ", ".join(f"#{b} {o}→{n} ({yrs:.1f}y)" for b, o, n, yrs in rows[:5]), "dim")
    if ui.confirm(f"re-derive {label} for {len(rows)} books? (Calibre closed; auto-backup)"):
        staleness.write(label, rows)
        ui.say("done ✓", "green")

def act_classify():
    a = classify.normalize(classify.build_parser().parse_args([]))
    # engine — with key/binary availability shown inline
    opts = []
    for i, e in enumerate(("apple", "claude", "openai", "gemini"), 1):
        if e == "apple":
            hint = "free, on-device" + ("" if os.path.exists(os.path.join(common.HERE, "afm"))
                                        else "  ⚠ ./afm not built (swiftc -O afm.swift -o afm)")
        else:
            hint = "key set ✓" if any(os.environ.get(k) for k in ENGINE_KEYS[e]) else "⚠ no API key in env"
        opts.append((str(i), e, hint))
    a.engine = {k: lbl for k, lbl, _ in opts}[ui.menu("engine", opts, default="1")]
    # scope
    scope = ui.menu("scope", [
        ("1", "incremental", "only books changed since the last wrangle (recommended, cheap)"),
        ("2", "untagged", f"books with fewer than {a.min_tags} tags"),
        ("3", "fresh", "restart the proposal — ⚠ a full cloud pass costs real money"),
    ], default="1")
    a.incremental, a.fresh = scope == "1", scope == "3"
    a.batch = ui.ask_int("batch size — books this run (0 = all)", 0)
    a.text_fallback = ui.confirm("sample the book's own text when the description is thin? (slower)", default=False)
    a = classify.normalize(a)
    # preview target count before any API call
    targets, _, _ = classify.gather(a)
    if not targets:
        ui.say("nothing to do — no candidate books for this scope ✓", "green"); return
    ui.say(f"\n→ {len(targets)} candidate books (already-proposed ones are skipped on resume)")
    if a.engine != "apple" and not ui.confirm(f"send up to {len(targets)} books to the {a.engine} API?"):
        return
    a.yes = True                              # the wizard confirmation above replaces the CLI spend gate
    classify.classify_run(a)
    if ui.confirm("\napply the proposal to the library now? (Calibre closed; auto-backup)", default=False):
        classify.apply_proposal()
        ui.say("done ✓", "green")

def act_review():
    if not os.path.exists(classify.PROP):
        ui.say("no pending proposal — run a classify pass first.", "dim"); return
    rows = list(csv.DictReader(open(classify.PROP)))
    tagged = [r for r in rows if r.get("added_tags", "").strip()]
    cnt = collections.Counter(t for r in tagged for t in r["added_tags"].split("; ") if t.strip())
    t = Table(box=box.SIMPLE, title=f"queued to apply — {len(tagged)} of {len(rows)} proposed books")
    t.add_column("vocab tag"); t.add_column("books", justify="right")
    for tag, c in cnt.most_common(15): t.add_row(tag, str(c))
    console.print(t)
    if os.path.exists(classify.RANK):
        r = Table(box=box.SIMPLE, title="top new-tag candidates (promote into defaults/classify_vocab.txt)")
        r.add_column("count", justify="right", style="cyan"); r.add_column("proposed tag")
        for row in list(csv.DictReader(open(classify.RANK)))[:15]: r.add_row(row["count"], row["proposed_tag"])
        console.print(r)
    if os.path.exists(classify.FAIL):
        n = sum(1 for _ in csv.DictReader(open(classify.FAIL)))
        if n: ui.say(f"⚠ {n} books failed classification — see {os.path.relpath(classify.FAIL, common.HERE)} "
                     "(recover with --engine apple)", "yellow")
    ui.say(f"\nfull proposal: {os.path.relpath(classify.PROP, common.HERE)}", "dim")
    if tagged and ui.confirm(f"apply these tags to {len(tagged)} books now? (Calibre closed; auto-backup)", default=False):
        classify.apply_proposal()
        ui.say("done ✓", "green")


def act_flow():
    """Guided maintenance run — the whole loop in the right order, every step skippable.
    Each act_* already previews and asks before writing, so this adds only sequencing."""
    ui.say("[bold]guided maintenance[/] — the loop in the right order: deterministic cleanup first "
           "(wrangle), then status, then AI tagging, then review. every step previews before "
           "writing and can be skipped.")
    steps = [("wrangle",   "normalize the new raw tags — FIRST, because junk tags inflate tag counts "
                           "and hide sparse books from the classifier", act_wrangle),
             ("staleness", "re-derive #status from #updated age (free)", act_staleness),
             ("classify",  "AI content tagging for new/changed books", act_classify),
             ("review",    "inspect + apply the pending proposal", act_review)]
    for i, (name, why, fn) in enumerate(steps, 1):
        console.rule(f"[bold]step {i}/4 · {name}[/]", style="cyan")
        ui.say(why, "dim")
        if not ui.confirm(f"run {name}?", default=True):
            ui.say(f"(skipped {name})", "dim"); continue
        try:
            fn()
        except SystemExit as e:
            if str(e): ui.error(str(e))
            if not ui.confirm("continue with the remaining steps?", default=True): return
        except KeyboardInterrupt:
            ui.say("\n(step cancelled)", "dim")
            if not ui.confirm("continue with the remaining steps?", default=True): return
    console.rule(style="green")
    ui.say("maintenance run complete ✓", "bold green")


MENU = [
    ("0", "maintenance", "guided run of the whole loop: wrangle → staleness → classify → review"),
    ("1", "setup",       "health check + first-run wizard (FanFicFare, columns, config)"),
    ("2", "audit",       "read-only dry-run report of every normalize pass"),
    ("3", "wrangle",     "normalize tags/fandoms/characters/genres — preview, then write"),
    ("4", "staleness",   "re-derive #status from #updated age — preview, then write"),
    ("5", "classify",    "AI content tagging with a live dashboard — propose, then apply"),
    ("6", "review",      "inspect the pending proposal + new-tag candidates"),
    ("7", "quit",        "also: q, or Ctrl+C at any prompt"),
]
ACTIONS = {"0": act_flow, "1": act_setup, "2": act_audit, "3": act_wrangle,
           "4": act_staleness, "5": act_classify, "6": act_review}


def run():
    library()                                 # fail fast with the clear CALIBRE_LIBRARY message
    if not ui.interactive():
        raise SystemExit("the wizard needs an interactive terminal — use the subcommands instead "
                         "(scourgify --help).")
    # first-launch guidance: an un-set-up library gets routed to setup before anything else
    info = snapshot()
    if info["setup_needed"]:
        ui.clear(); header(info)
        ui.say("this library isn't fully set up yet — the tools need their columns and config.toml.", "yellow")
        if ui.confirm("run setup now?", default=True):
            try:
                act_setup()
            except SystemExit as e:
                if str(e): ui.error(str(e))
            except (KeyboardInterrupt, EOFError):
                ui.say("\n(cancelled)", "dim")
            ui.pause()
    while True:
        ui.clear()
        info = snapshot()
        header(info)
        try:
            choice = ui.menu("what do you want to do?", MENU, default=recommend(info), also=("q",))
        except (KeyboardInterrupt, EOFError):  # Ctrl+C / Ctrl+D at the menu = quit cleanly
            console.print()
            return
        if choice in ("7", "q"):
            return
        console.print()
        try:
            ACTIONS[choice]()
        except SystemExit as e:               # guardrails/aborts return to the menu instead of exiting
            if str(e): ui.error(str(e))
        except (KeyboardInterrupt, EOFError):  # Ctrl+C mid-action = cancel back to the menu
            ui.say("\n(cancelled — nothing written beyond what was already confirmed)", "dim")
        console.print()
        ui.pause()


if __name__ == "__main__":
    run()
