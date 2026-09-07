#!/usr/bin/env python3
"""Manual PTY smoke test — proves the wizard starts under a REAL terminal. Deliberately NOT named
test_* — it shells out to `uv run`, so the CI glob skips it; run it locally before a release:

    uv run tests/drive_wizard.py

Scope is deliberately one lap: process starts, header renders, landing menu appears, `q` quits
cleanly. That covers what an in-process test cannot — rich's Prompt against a real tty, terminal
detection, the checklist's live redraw. The interaction FLOWS moved to tests/test_wizard_flow.py,
which drives the same stages with canned answers in milliseconds instead of regex-matching a
transcript for 15 seconds. Rendering aesthetics stay a human's job."""
import os, pty, re, select, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "src"))
from fixture_db import build
from scourgify import common

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|[\r\x07]")

# One lap: the landing menu appears, we quit. Flow coverage lives in tests/test_wizard_flow.py.
MENU_KEYS = ["q"]
RULES = []                                   # no mid-flow prompts to answer on a bare quit

CHECKS = [
    (r"Fixture Book|2 books", "header shows the fixture library"),
    (r"what would you like to do\?", "landing menu appeared under a real pty"),
    (r"pick up where you left off", "quit exits through the clean-exit path"),
]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, "library"); os.makedirs(lib)
        cols = [(c, {}) for c in ("fandoms", "characters", "relationships", "genres", "status",
                                  "updated", "wrangled", "synopsized")]
        build(os.path.join(lib, "metadata.db"),
              [{"id": 1, "title": "Fixture Book A", "added": "2026-01-01 10:00:00",
                "desc": "A description long enough to classify. " * 3, "tags": ["Keeper"]},
               {"id": 2, "title": "Fixture Book B", "added": "2026-02-01 10:00:00",
                "desc": "Another perfectly serviceable description. " * 3}],
              custom=cols).close()
        home = os.path.join(td, "home"); os.makedirs(home)
        open(os.path.join(home, "config.toml"), "w", encoding="utf-8").write('[columns]\n[behavior]\n[overrides]\ndir = "overrides"\n')

        # data_dir() is uuid-scoped now — resolve it here (in THIS process, briefly pointed at the
        # same lib/home the child subprocess below gets) so the seeded proposal lands exactly
        # where the wizard subprocess will look for it.
        old = {k: os.environ.get(k) for k in ("SCOURGIFY_HOME", "CALIBRE_LIBRARY")}
        os.environ["SCOURGIFY_HOME"], os.environ["CALIBRE_LIBRARY"] = home, lib
        common.clear_uuid_cache()
        try:
            data_dir = common.data_dir()
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            common.clear_uuid_cache()
        os.makedirs(data_dir, exist_ok=True)
        prop_path = os.path.join(data_dir, "classify_proposal.csv")
        open(prop_path, "w", encoding="utf-8").write("book_id,title,added_tags,proposed_new\n"
                                   "1,Fixture Book A,Time Loop,\n2,Fixture Book B,Fix-It,\n")

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
            if re.search(r"choose \[w(?:/[\w]+)+/q\]", new) and menu_i < len(MENU_KEYS):
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
        print("=" * 72)
        if not ok:
            print("--- clean transcript tail ---"); print(clean[-3000:])
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
