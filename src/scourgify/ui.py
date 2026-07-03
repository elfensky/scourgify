#!/usr/bin/env python3
"""Terminal UI helpers for the wizard (wizard.py) — the one surface where rich is REQUIRED.

The core tools (wrangle/classify/staleness) keep their try/except rich fallbacks and
`_writer.py` stays stdlib-only under calibre-debug's Python; never import this module
from them. Pattern follows lintle's term.py: one shared Console, prompt helpers that
validate input, and interactivity detection that requires real TTYs."""
import os, sys
try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich.table import Table
except ImportError:
    raise SystemExit("the wizard needs the `rich` package:  python3 -m pip install rich")

console = Console()


def interactive():
    """Real wizard sessions only: stdin AND stdout are TTYs, no CI/NONINTERACTIVE override."""
    if os.environ.get("CI") or os.environ.get("NONINTERACTIVE"):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def clear():
    console.clear()

def say(msg, style=""):
    console.print(msg, style=style)

def error(msg):
    console.print(f"[bold red]✗[/] {msg}")

def panel(renderable, title=None, style="cyan"):
    console.print(Panel(renderable, title=title, border_style=style, padding=(0, 1)))


def menu(title, options, default=None, also=()):
    """Render a keyed menu and return the chosen key. options = [(key, label, hint), ...];
    `also` lists extra accepted keys that aren't rendered (e.g. a 'q' quit alias)."""
    t = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    t.add_column(style="bold cyan", justify="right"); t.add_column(); t.add_column(style="dim")
    for k, label, hint in options:
        t.add_row(k, label, hint)
    panel(t, title=title)
    keys = [k for k, _, _ in options] + list(also)
    return Prompt.ask("choose", choices=keys, default=default or keys[0], console=console)


def confirm(msg, default=False):
    return Confirm.ask(msg, default=default, console=console)

def ask_int(msg, default=0):
    return IntPrompt.ask(msg, default=default, console=console)


def pause():
    try:
        Prompt.ask("[dim]enter to return to the menu[/]", default="", show_default=False, console=console)
    except (EOFError, KeyboardInterrupt):
        pass
