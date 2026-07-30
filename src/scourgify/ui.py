#!/usr/bin/env python3
"""Terminal UI helpers for the wizard (wizard.py) — the one surface where rich is REQUIRED.

The core tools (wrangle/classify/staleness) keep their try/except rich fallbacks and
`_writer.py` stays stdlib-only under calibre-debug's Python; never import this module
from them. Pattern follows lintle's term.py: one shared Console, prompt helpers that
validate input, and interactivity detection that requires real TTYs."""
import re
from scourgify import common
from scourgify.common import interactive    # re-export: the ONE tty policy lives in common
try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
except ImportError:
    raise SystemExit("the wizard needs the `rich` package (a declared dependency, so this means a "
                     "partial install):\n  uv tool install --force scourgify   "
                     "— or from a checkout:  uv run scourgify")

console = Console()


def clear():
    if common.scripted(): return          # an ANSI screen-wipe mid-transcript is noise
    console.clear()

def say(msg, style=""):
    console.print(msg, style=style)

def error(msg):
    console.print(f"[bold red]✗[/] {msg}")

def panel(renderable, title=None, style="cyan"):
    console.print(Panel(renderable, title=title, border_style=style, padding=(0, 1)))


def menu(title, options, default=None, also=()):
    """Render a keyed menu and return the chosen row's symbolic ID.

    options = [(key, id, label, hint), ...]. The KEY is what the user types — presentation. The
    ID is what the caller dispatches on. They are deliberately separate: several menus build
    their rows conditionally, so a key's meaning is position-dependent while an id's never is.
    Dispatching on keys is how "2" came to mean 'review 1-by-1' in one proposal menu and
    'discard' in the other.

    A row with id=None is rendered dimmed and cannot be chosen, but KEEPS ITS SLOT — so a number
    the user has memorised never shifts under them when a row stops applying.

    `default` is an ID too, for the same reason (and a wrong one raises here rather than being
    silently returned: rich hands a default straight back without validating it against the
    choices, so a stale default used to escape on the interactive path only). `also` lists extra
    accepted keys that aren't rendered (e.g. a 'q' quit alias); they return themselves."""
    t = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    t.add_column(style="bold cyan", justify="right"); t.add_column(); t.add_column(style="dim")
    for k, ident, label, hint in options:
        if ident is None: t.add_row(f"[dim]{k}[/]", f"[dim]{label}[/]", f"[dim]{hint}[/]")
        else:             t.add_row(k, label, hint)
    panel(t, title=title)
    by_key = {k: i for k, i, _, _ in options if i is not None}
    by_key.update({k: k for k in also})                # unrendered aliases stand for themselves
    keys = list(by_key)
    if default is not None and default not in by_key.values():
        # a programmer error, not user input — loud and immediate, and not strippable by -O
        raise ValueError(f"menu {title!r}: default {default!r} is not an enabled row")
    dkey = next((k for k, i in by_key.items() if i == default), keys[0])
    if common.scripted():
        ans = common.script_next(f"menu {title!r} (choices: {'/'.join(keys)})").strip()
        if ans == "": ans = dkey                       # '' = pressing enter = take the default
        if ans not in by_key:
            raise common.ScriptError(f"menu {title!r}: {ans!r} is not one of {'/'.join(keys)}")
        say(f"[dim]choose:[/] {ans}")                  # echo, so a captured run reads like a session
        return by_key[ans]
    return by_key[Prompt.ask("choose", choices=keys, default=dkey, console=console)]


def ask_int(msg, default, lo=1, hi=None):
    """A bounded integer prompt (the batch size). Interactive: re-ask on garbage. SCRIPTED: raise
    ScriptError instead — re-asking would silently eat the next canned answer and shift the whole
    queue by one, which is exactly the drift this seam exists to make loud."""
    while True:
        if common.scripted():
            raw = common.script_next(f"number {msg!r}").strip()
            say(f"[dim]{msg}[/] {raw or default}")
        else:
            raw = Prompt.ask(msg, default=str(default), console=console).strip()
        if raw == "": return default
        try:
            n = int(raw)
            if n < lo or (hi is not None and n > hi): raise ValueError
            return n
        except ValueError:
            bound = f"{lo}-{hi}" if hi is not None else f"at least {lo}"
            if common.scripted():
                raise common.ScriptError(f"number {msg!r}: {raw!r} is not an integer ({bound})")
            error(f"expected a whole number ({bound})")


def confirm(msg, default=False):
    if common.scripted():
        ans = common.script_bool(msg, default)
        say(f"[dim]{msg}[/] {'y' if ans else 'n'}")
        return ans
    return Confirm.ask(msg, default=default, console=console)


def pause():
    if common.scripted(): return           # it asks nothing, it only waits
    try:
        Prompt.ask("[dim]enter to return to the menu[/]", default="", show_default=False, console=console)
    except (EOFError, KeyboardInterrupt):
        pass


def checklist(title, items, subtitle=""):
    """Numbered per-item review, all items pre-ticked (accepted). Returns
    (accepted_idx, rejected_idx, action) where action ∈ {'apply','skip','quit'} and the
    two index lists partition range(len(items)). A pure widget — no logging, no writing;
    callers interpret the action. `items` is a list of display strings."""
    if not items:
        return [], [], "apply"
    ticked = [True] * len(items)
    while True:
        t = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
        t.add_column(justify="right", style="dim"); t.add_column(); t.add_column()
        for i, s in enumerate(items):
            on = ticked[i]
            t.add_row(str(i + 1), "[green]☑[/]" if on else "[dim]☐[/]", s if on else f"[dim]{s}[/]")
        if subtitle: say(f"[dim]{subtitle}[/]")
        panel(t, title=title)
        say("[dim]toggle #s to reject · ⏎ apply ticked · a all · s skip · q quit (leave rest)[/]")
        if common.scripted():
            # ponytail: no key validation here — the toggle loop below already ignores anything
            # that isn't an in-range digit, and a script of pure garbage self-limits by exhausting
            # the queue into a ScriptError rather than spinning.
            raw = common.script_next(f"checklist {title!r}")
            say(f"[dim]choose:[/] {raw or '⏎'}")
        else:
            raw = Prompt.ask("choose", default="", show_default=False, console=console)
        raw = raw.strip().lower()
        if raw in ("q", "quit"): return [], list(range(len(items))), "quit"
        if raw in ("s", "skip"): return [], list(range(len(items))), "skip"
        if raw in ("a", "all"): return list(range(len(items))), [], "apply"
        if raw == "":
            return ([i for i in range(len(items)) if ticked[i]],
                    [i for i in range(len(items)) if not ticked[i]], "apply")
        for tok in re.split(r"[ ,]+", raw):
            if tok.isdigit() and 1 <= int(tok) <= len(items): ticked[int(tok) - 1] ^= True
