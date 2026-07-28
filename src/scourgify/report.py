#!/usr/bin/env python3
"""Rich-optional rendering for the core tools (wrangle / classify / staleness) — the ONE owner
of the rich-or-plain policy. Tools describe WHAT to show (title, columns, rows, tree nodes);
this module decides HOW: rich tables/trees when rich is importable, aligned plain text otherwise
(scripting/CI without rich must keep working — CLAUDE.md). A cell may be a plain string or a
(text, style) tuple; the style is applied under rich and dropped in plain mode, so call sites
never write a second renderer. The wizard's own surfaces stay in ui.py (rich required);
_writer.py imports neither. Also home to the live classify Dashboard + sparkline (rich renders
them live; without rich the Dashboard degrades to a checkpoint line)."""
import collections
import time

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import Progress, BarColumn, TextColumn, MofNCompleteColumn, TimeRemainingColumn
    from rich.table import Table
    from rich.text import Text
    from rich.tree import Tree
    console = Console()
    _err = Console(stderr=True)     # live displays render on stderr so piped stdout stays clean
    RICH = True
except ImportError:
    RICH = False
    console = _err = None


def _text(c) -> str:
    return c if isinstance(c, str) else str(c[0])


def _markup(c) -> str:
    return c if isinstance(c, str) else f"[{c[1]}]{c[0]}[/]"


def say(msg, style: str = "") -> None:
    """One styled (or plain) line — rich markup allowed only via the style argument."""
    if RICH: console.print(msg, style=style)
    else: print(msg)


def table(title: str, cols: list, rows: list, right: tuple = ()) -> None:
    """cols: header strings; rows: lists of cells (str or (text, style)); right: indices
    of right-aligned columns."""
    if RICH:
        t = Table(title=title, title_justify="left")
        for i, h in enumerate(cols):
            t.add_column(h, justify="right" if i in right else "left")
        for r in rows:
            t.add_row(*[_markup(c) for c in r])
        console.print(t)
        return
    plain = [[_text(c) for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in plain]) for i, h in enumerate(cols)]
    def fmt(cells):
        return "  ".join(c.rjust(widths[i]) if i in right else c.ljust(widths[i])
                         for i, c in enumerate(cells)).rstrip()
    print(f"\n{title}")
    print("  " + fmt(cols))
    for r in plain: print("  " + fmt(r))


def tree(title: str, nodes: list) -> None:
    """nodes: [(label, [line, ...])] — a two-level tree (rich Tree / indented plain text)."""
    if RICH:
        t = Tree(f"[bold]{title}[/]")
        for label, lines in nodes:
            node = t.add(label)
            for ln in lines: node.add(_markup(ln))
        console.print(t)
        return
    print(f"  {title}")
    for label, lines in nodes:
        print(f"  {_text(label)}")
        for ln in lines: print(f"      {_text(ln)}")


# ---- live run display ----
def sparkline(vals: list, width: int = 28) -> str:
    """Unicode sparkline of a numeric series (last `width` points), scaled to its max."""
    vals = [v for v in vals][-width:]
    if not vals: return ""
    blocks = "▁▂▃▄▅▆▇█"
    hi = max(vals)
    if hi <= 0: return blocks[0] * len(vals)
    return "".join(blocks[min(7, int(v * 8 / hi))] for v in vals)


class Dashboard:
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
                                 MofNCompleteColumn(), TimeRemainingColumn(), console=_err)
            self.task = self.prog.add_task("", total=self.total)
            self.live = Live(self._render(), console=_err, refresh_per_second=4)
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
