# scourgify — user guide

*The friendly version. For the full technical story, see the [README](../README.md).*

## What it does

If you download fanfiction into [Calibre](https://calibre-ebook.com) with FanFicFare, your
library slowly fills with messy metadata: the same fandom spelled five ways, junk tags like
"Complete" and "No Beta", characters filed under genres, and thousands of one-off tags nobody
can browse. scourgify is a cleanup crew for that mess. It:

- **tidies** what's there (merges spellings, drops junk, moves things to the right column),
- **keeps status fresh** (a story untouched for 5 years becomes "Abandoned" on its own),
- and can **read each story's description with an AI** and add real content tags
  ("Time Loop", "Fix-It", "Slow Burn") from a controlled list, so tags stay browsable.

Three promises it keeps:

1. **It always shows you first.** Every step is a dry-run with a report; nothing is written
   until you say yes.
2. **Every write is undoable.** It snapshots your database before touching it —
   `scourgify rollback` restores.
3. **It never fights you twice.** Reject a change once and it can learn a personal rule so
   the same change is never suggested again.

## Setup (once)

```bash
uv tool install scourgify          # needs uv: https://docs.astral.sh/uv/

# tell it where your library lives (the folder containing metadata.db)
export CALIBRE_LIBRARY="$HOME/Calibre/fanfiction"    # put this in your shell profile
```

Later on, `uv tool upgrade scourgify` gets you the newest version, and
`uv tool uninstall scourgify` removes it cleanly. Your settings and personal rules live
outside the install, so upgrading never touches them.

Then just run:

```bash
scourgify
```

The wizard checks your library, offers to create the columns it needs (`#fandoms`,
`#characters`, `#status`, …), and writes its config. Answer the questions — that's setup done.

## Day to day: run the wizard

After downloading new stories, run `scourgify` again. You'll get a menu:

```
w  full maintenance run     ← the whole routine, guided, in the right order
1  wrangle                  tidy raw tags/fandoms/characters
2  staleness                refresh #status from last-update age (free)
3  synopsis                 give every book a real description (free, on-device)
4  classify                 AI content tagging (new/changed books only)
5  review                   look at the AI's suggestions, then apply
6  promote                  decide if brand-new tags join your vocabulary
7  backfill                 give newly promoted tags to the books that inspired them
8  overrides                turn your rejections into permanent personal rules
q  quit
```

**If in doubt, press Enter** — `w` walks everything in the right order, showing each report
and asking before each write. Blue `●` markers on the menu mean "there's unfinished work
here" (a pending proposal, undecided candidates, and so on).

The one rule: **close Calibre before saying yes to a write.** Reading works anytime, but
writes refuse while Calibre is open (it locks the database). The wizard warns you.

## The steps, in plain words

**Wrangle** — deterministic cleanup, no AI, free. Merges fandom spellings ("Naruto (Anime &
Manga)" → "Naruto"), folds character variants, drops junk tags, routes misfiled values to
the right column. The report shows totals plus a per-book tree of the unusual changes, and a
SAFETY line proving no book loses its last fandom or character.

**Staleness** — free and instant. In-progress stories age into Hiatus (2 years quiet) and
Abandoned (5 years). Completed stories are never touched. Self-correcting if a story updates.

**Synopsis** — free, private, and slow. Fanfiction descriptions are a lottery: some are proper
back-cover blurbs, plenty are "summary inside", an update schedule, or nothing at all. This step
looks at each one and **keeps the good ones exactly as the author wrote them** — no AI rewriting a
description that was already fine. Only the useless ones get replaced, by reading the book itself
on your own machine and writing a real blurb: premise, characters, what's at stake, and a themes
line. Never the ending — the description stays safe to browse.

It runs on-device, so it costs nothing and nothing leaves your Mac, but it takes about a minute
per book it has to write. That's fine: it asks how many to do this run, remembers where it got to
(the `#synopsized` column), and picks up there next time. Leave it chewing in the background over
a few evenings.

**One thing to do first:** in Calibre, go to Preferences → Plugins → FanFicFare → Customize →
Standard Columns and tick **New Only** next to Comments. Without it, the next metadata re-fetch
overwrites every description this step writes. scourgify refuses to start until you do, and
`scourgify setup` offers to set it for you.

Books with a good description also benefit indirectly: the next step, classify, tags from the
description, so a book that had nothing to read now has something.

**Classify** — the AI step. It picks the books that are new or changed since last time,
shows you *exactly* how many, and prices each engine before you commit:

```
1  apple     free, on-device        ·  free for 9 books
2  claude    no API key in env      ·  ~$0.01 for 9 books
3  openai    key set ✓              ·  ~$0.00 for 9 books
c  compare   try 5 sample books on every usable engine first
```

`apple` is free and private (on-device, macOS with Apple Intelligence); cloud engines need
an API key in your environment (`OPENAI_API_KEY`, `GEMINI_API_KEY`, …) and cost roughly a
cent per handful of books. `compare` runs a few sample books through every engine so you can
judge quality before spending. Nothing is applied yet — the AI only writes a **proposal**.

**Review** — the proposal on your terms. Apply it all, or walk it **1-by-1**: each book's
suggested tags appear as a checklist; untick the ones the AI got wrong, or skip the whole
book for later. Skipped books are never lost — they stay in the proposal for next time.

**Promote** — the AI sometimes suggests tags that aren't in the vocabulary yet ("Kingdom
Building"). Instead of letting tags multiply freely, two AI passes argue about each one
(promote it? alias it to an existing tag? reject it?) and **you referee the verdicts**
before anything joins your vocabulary.

**Backfill** — once a tag is promoted, the books that originally suggested it get it —
automatically, no AI, free.

**Overrides** — anything you rejected during 1-by-1 review can become a personal rule
(`scourgify overrides`), so that exact change is never proposed again. Your rules live in
`~/.config/scourgify/overrides/` and survive upgrades.

## When something goes wrong

```bash
scourgify rollback --list     # see your database snapshots (kept: last 20)
scourgify rollback            # restore the newest one (also reversible)
```

- **"Calibre is running — close it first"** — that's the safety lock. Close Calibre, retry.
- **A book failed classification ("blocked")** — Gemini refuses a material share of mature
  content: 7 of 50 books (14%) on a random sample of a real fanfiction library, so expect to
  route roughly one book in seven to another engine. Recovered books drop off
  `classify_failures.csv` automatically once a re-run succeeds.
  Re-run with another engine: `scourgify classify --engine openai` (or `apple`).
- **A whole apply refused** — the guards also stop anything that would mass-empty a column.
  Your data and your proposal are left untouched; nothing partial is written.

## Cheat sheet

```bash
scourgify                        # the wizard — start here
scourgify audit                  # detailed read-only report of every cleanup pass
scourgify apply --apply          # wrangle from the CLI (add --step for 1-by-1)
scourgify staleness --apply      # refresh #status
scourgify synopsis --apply --batch 200   # write real descriptions for 200 books (free)
scourgify classify --incremental # AI-tag only new/changed books
scourgify classify --books 1,2,3 # AI-tag exactly these books (also: apply --books, staleness --books)
scourgify classify --apply       # write the reviewed proposal (add --step for 1-by-1)
scourgify promote                # adjudicate new-tag candidates
scourgify promote --backfill     # tag the books that inspired promoted tags
scourgify overrides --apply      # turn your rejections into permanent rules
scourgify rollback --list        # your undo history
```

Costs to remember: everything is free except **classify** and **promote** on a cloud engine
— and both show you the count and price, then ask. A full-library cloud pass over thousands
of books is real money (tens of euros — ~$25 for gemini over ~8k books); the wizard's default scope (new/changed only) is pennies.
