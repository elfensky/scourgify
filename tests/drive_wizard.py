#!/usr/bin/env python3
"""Manual PTY smoke test for the interactive wizard — drives the REAL `scourgify` TTY surface
(menus, prompts, checklist) inside a pseudo-terminal against a throwaway fixture library, so the
interaction FLOW is checked by a script instead of a human. Deliberately NOT named test_* — it
shells out to `uv run` and takes ~15s, so the CI glob skips it; run it locally before a release:

    uv run tests/drive_wizard.py

What it drives: landing menu → every task's no-write path (wrangle skip, staleness, classify
scope-skip, review 1-by-1 with every book SKIPPED, promote/backfill/overrides empty paths) → quit.
What it pins: the menu loop survives a full lap; scope-skip touches no engine; and the data-loss
guarantee — a step review that only skips leaves the proposal file byte-identical (the bug class
fixed in ff74991/05d1dbe). Rendering aesthetics stay a human's job; this checks behavior."""
import os, pty, re, select, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "src"))
from fixture_db import build

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|[\r\x07]")

# Task keys sent each time the landing menu re-appears (one lap over the toolset, then quit).
MENU_KEYS = ["1", "2", "3", "4", "5", "6", "7", "q"]

# prompt pattern (matched against NEW transcript text only) -> canned answer.
RULES = [
    (r"choose \[n/a/s\]", "s\n"),                       # classify scope -> skip (no engine, no cost)
    (r"choose \[a/r/s\]", "s\n"),                       # wrangle apply menu -> skip
    (r"choose \[a/r/k/d\]", "r\n"),                     # review menu -> 1-by-1 step review
    (r"⏎ apply ticked", "s\n"),                         # checklist -> SKIP every book (nothing decided)
    (r"natural next step.*\(y\)", "n\n"),               # standalone-task follow-up -> back to the menu
    (r"re-derive .*\(True\)", "n\n"),                   # staleness confirm (empty fixture: shouldn't appear)
    (r"remaining steps\?", "n\n"),                      # workflow guard (shouldn't appear)
]

CHECKS = [
    (r"Fixture Book|2 books", "header shows the fixture library"),
    (r"what would you like to do\?", "landing menu appeared"),
    (r"\(skipped — nothing tagged\)", "classify scope-skip honored"),
    (r"consistent ✓|re-derive", "staleness stage ran"),
    (r"⏎ apply ticked", "1-by-1 checklist rendered"),
    (r"nothing decided — proposal left untouched", "skip-all leaves the proposal alone"),
    (r"no new-tag candidates yet|candidates to adjudicate|previously-adjudicated", "promote stage ran"),
    (r"backfill applies vocab-promoted tags|done ✓", "backfill stage ran"),
    (r"no rejected changes logged|no rejects logged yet|auto-suppressible", "overrides stage ran"),
]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, "library"); os.makedirs(lib)
        cols = [(c, {}) for c in ("fandoms", "characters", "relationships", "genres", "status", "updated", "wrangled")]
        build(os.path.join(lib, "metadata.db"),
              [{"id": 1, "title": "Fixture Book A", "added": "2026-01-01 10:00:00",
                "desc": "A description long enough to classify. " * 3, "tags": ["Keeper"]},
               {"id": 2, "title": "Fixture Book B", "added": "2026-02-01 10:00:00",
                "desc": "Another perfectly serviceable description. " * 3}],
              custom=cols).close()
        home = os.path.join(td, "home"); os.makedirs(os.path.join(home, "data"))
        open(os.path.join(home, "config.toml"), "w").write('[columns]\n[behavior]\n[overrides]\ndir = "overrides"\n')
        prop_path = os.path.join(home, "data", "classify_proposal.csv")
        open(prop_path, "w").write("book_id,title,added_tags,proposed_new\n"
                                   "1,Fixture Book A,Time Loop,\n2,Fixture Book B,Fix-It,\n")
        prop_before = open(prop_path).read()

        env = {**os.environ, "SCOURGIFY_HOME": home, "CALIBRE_LIBRARY": lib,
               "TERM": "xterm-256color", "COLUMNS": "100", "LINES": "40"}
        master, slave = pty.openpty()
        proc = subprocess.Popen(["uv", "run", "scourgify"], cwd=REPO, env=env,
                                stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)

        transcript, pos, menu_i = "", 0, 0
        deadline, last_progress = time.time() + 120, time.time()
        while proc.poll() is None and time.time() < deadline:
            r, _, _ = select.select([master], [], [], 0.3)
            if r:
                try: chunk = os.read(master, 65536).decode(errors="replace")
                except OSError: break
                transcript += chunk
            clean = ANSI.sub("", transcript)
            new = clean[pos:]
            sent = None
            if re.search(r"choose \[w/1/2/3/4/5/6/7/q\]", new) and menu_i < len(MENU_KEYS):
                sent = MENU_KEYS[menu_i]; menu_i += 1
                os.write(master, (sent + "\n").encode())
            else:
                for rx, ans in RULES:
                    if re.search(rx, new):
                        sent = ans; os.write(master, ans.encode()); break
            if sent is not None:
                pos, last_progress = len(clean), time.time()
                time.sleep(0.2)
            elif time.time() - last_progress > 30:
                print("STALLED — no prompt matched for 30s; tail:\n" + clean[-1500:]); proc.kill(); break
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill()

        clean = ANSI.sub("", transcript)
        ok = proc.returncode == 0
        print("=" * 72)
        print(f"  {'PASS' if proc.returncode == 0 else 'FAIL'}  wizard exited cleanly (rc={proc.returncode})")
        for rx, what in CHECKS:
            hit = bool(re.search(rx, clean)); ok &= hit
            print(f"  {'PASS' if hit else 'FAIL'}  {what}")
        intact = open(prop_path).read() == prop_before
        ok &= intact
        print(f"  {'PASS' if intact else 'FAIL'}  proposal byte-identical after skip-all review (data-loss pin)")
        print("=" * 72)
        if not ok:
            print("--- clean transcript tail ---"); print(clean[-3000:])
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
