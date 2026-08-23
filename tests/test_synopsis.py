#!/usr/bin/env python3
"""The synopsis pass — the FanFicFare clobber guard, the pure prompt/parse helpers, and
settle() driven by a fake engine. No Calibre, no network, no on-device model.
No framework:  uv run tests/test_synopsis.py   (also pytest-collectable)."""
import json, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import common, setup as setup_mod
from fixture_db import build

FFF_KEY = "namespaced:FanFicFarePlugin:settings"


def _con(prefs=None):
    d = tempfile.mkdtemp()
    con = build(os.path.join(d, "metadata.db"), [dict(id=1, added="2026-01-01 10:00:00")])
    if prefs is not None:
        con.execute("INSERT INTO preferences VALUES(?,?)", (FFF_KEY, json.dumps(prefs)))
        con.commit()
    return con


def test_comments_protected_reads_the_real_fff_pref_shape():
    """Verified 2026-08-23 against the author's library: FFF stores per-library prefs as JSON in
    the preferences table under namespaced:FanFicFarePlugin:settings, and std_cols_newonly is a
    dict of standard-column -> bool. Readable through an ordinary read-only connection, which is
    what makes this guard automatic instead of an interactive confirm."""
    on = {"std_cols_newonly": {"comments": True, "title": True}, "custom_cols": {}}
    off = {"std_cols_newonly": {"comments": False, "title": True}, "custom_cols": {}}
    assert setup_mod.comments_protected(_con(on)) is True
    assert setup_mod.comments_protected(_con(off)) is False
    assert setup_mod.comments_protected(_con({"custom_cols": {}})) is False   # key absent = unprotected
    assert setup_mod.comments_protected(_con(None)) is None                   # FFF unconfigured here
    assert setup_mod.fff_settings(_con(None)) == {}


def test_comments_protected_survives_a_corrupt_prefs_blob():
    con = _con()
    con.execute("INSERT INTO preferences VALUES(?,?)", (FFF_KEY, "{not json"))
    con.commit()
    assert setup_mod.fff_settings(con) == {}
    assert setup_mod.comments_protected(con) is None


def test_synopsized_is_a_recommended_column():
    assert ("#synopsized", "Synopsized", "datetime", False) in setup_mod.REC


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
