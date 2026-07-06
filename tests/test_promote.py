#!/usr/bin/env python3
"""Regression tests for tag promotion features.
No framework needed:  uv run tests/test_promote.py   (also collectable by pytest).
No Calibre, no library, no network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def test_ask_retry_success_and_block():
    from scourgify.classify import ask_retry
    class OK:
        def ask(self, p): return "yes"
    assert ask_retry(OK(), "x") == ("yes", "")

    class Blocked:
        def ask(self, p): raise RuntimeError("blocked:PROHIBITED")
    out, err = ask_retry(Blocked(), "x")
    assert out == "" and err.startswith("blocked:")

    class Flaky:                                 # fails once, then succeeds — but tries=1 gives up immediately
        def __init__(self): self.n = 0
        def ask(self, p):
            self.n += 1
            if self.n == 1: raise ValueError("429")
            return "ok"
    out, err = ask_retry(Flaky(), "x", tries=1)
    assert out == "" and "ValueError" in err


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
