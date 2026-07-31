#!/usr/bin/env python3
"""The ONE owner of the user's overrides/ files — their location (cfg[overrides].dir), headers,
delimiters, the append-if-absent rule, and the format-semantic readers — plus the `--step`
reject → overrides subsystem (issue #11).

Writers go through append_lines/append_rows (promote's vocab/trope/alias folds, the
rejects→overrides flow below) and the format readers live next to them (merge_vocab's '-term'
removal, read_aliases' delimiter sniff), so a format decided here can't be mis-read elsewhere.
Appends honor an existing file's delimiter (sniffed like wrangle.read_tropes), so mixed-writer
files can't corrupt.

`apply --step` walks each book's unique edits and lets you untick individual changes; the rejected
ones are logged to data/rejects.csv. `scourgify overrides` then turns the deterministic (wrangle)
rejects into identity-override lines so the same wrong change never recurs."""
import os, csv, time, collections
from scourgify.artifacts import read_rows as read_csv
from scourgify.common import DEFAULTS as DEF, load_config, user_dir, norm, read_lines, ro_connect


def overrides_dir(cfg: dict | None = None) -> str:
    """The user's overrides dir — cfg[overrides].dir under user_dir(). The ONE resolution:
    wrangle.load_maps, setup's health check, and every reader/writer here go through it,
    so a relocated dir can never split writers from readers."""
    cfg = cfg or load_config()
    return os.path.join(user_dir(), cfg.get("overrides", {}).get("dir", "overrides"))


def ov_path(name: str) -> str:
    """A file inside the user's overrides dir (config-resolved)."""
    return os.path.join(overrides_dir(), name)


def _delim_of(path: str, default: str = ",") -> str:
    """The delimiter an existing override CSV already uses (sniffed like wrangle.read_tropes);
    `default` for a new file."""
    if os.path.exists(path):
        first = open(path).readline()
        if ";" in first and first.count(";") >= first.count(","): return ";"
        if "," in first: return ","
    return default


def append_lines(path: str, lines: list) -> list:
    """Append plain lines to a list file (vocab / allowlists), skipping ones already present.
    -> the lines actually added."""
    return _append_override(path, lines)


def merge_vocab(terms: list, path: str | None = None) -> list:
    """Apply the overrides vocab file to a base term list: a plain line appends (case-insensitive
    dedup), '-term' removes (later lines win — a promote append at the end beats an old removal),
    comments/blanks ignored. The reader half of append_lines' format, owned next to it."""
    path = path or ov_path("classify_vocab.txt")
    terms = list(terms)
    if os.path.exists(path):
        for l in open(path):
            l = l.strip()
            if not l or l.startswith("#"): continue
            if l.startswith("-"): terms = [t for t in terms if t.lower() != l[1:].strip().lower()]
            elif l.lower() not in {t.lower() for t in terms}: terms.append(l)
    return terms


def read_aliases(path: str | None = None) -> dict:
    """candidate(lower) -> target from promote_aliases.csv, sniffing the delimiter the same way
    append_rows preserves it — a ';' file round-trips instead of parsing as one column. {} if absent."""
    path = path or ov_path("promote_aliases.csv")
    out = {}
    if os.path.exists(path):
        for r in csv.DictReader(open(path), delimiter=_delim_of(path)):
            if r.get("candidate") and r.get("target"):
                out[r["candidate"].strip().lower()] = r["target"].strip()
    return out


def append_rows(path: str, header: list, rows: list) -> int:
    """Append CSV rows, honoring the file's existing delimiter (default ','), writing the header
    on first write, skipping rows already present. -> how many were added."""
    delim = _delim_of(path)
    new = not os.path.exists(path)
    existing = set() if new else {tuple(r) for r in csv.reader(open(path), delimiter=delim)}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    added = 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f, delimiter=delim)
        if new: w.writerow(header)
        for r in rows:
            key = tuple(str(x) for x in r)
            if key in existing: continue
            w.writerow(r); existing.add(key); added += 1
    return added


# ---------------- 1-by-1 review (`--step`): reconstruct + rejects → overrides ----------------
def _edit_label(kind: str, where: str, before: str, after: str) -> str:
    """A checklist display line for one unique edit (label-space `where`)."""
    body = {"rename": f"{before} → {after}", "move": f"{before}  → {where.split('→')[-1].strip()}",
            "drop": f"− {before} (dropped)", "add": f"+ {after}"}[kind]
    col = where.split("→")[0].strip() if kind == "move" else where
    return f"{col:<14} {body}"


def reconstruct(nd_lab: dict, orig_lab: dict, rejected: list) -> dict:
    """Revert only the rejected edits from the full transform result.
    nd_lab/orig_lab: {label: iterable of values} (new state / original). rejected: iterable of
    (kind, where, before, after) in label space (where='label', or 'src → dst' for a move).
    Returns {label: sorted list} for labels that STILL differ from the original — the accepted
    net change. Invert per kind: rename→restore before, drop→re-add, add→remove, move→move back."""
    rev = {lab: set(vs) for lab, vs in nd_lab.items()}
    for lab, vs in orig_lab.items(): rev.setdefault(lab, set(vs))
    for kind, where, before, after in rejected:
        if kind == "rename":
            rev[where].discard(after); rev[where].add(before)
        elif kind == "drop":
            rev[where].add(before)
        elif kind == "add":
            rev[where].discard(after)
        elif kind == "move":
            src, dst = [x.strip() for x in where.split("→")]
            rev[dst] = {x for x in rev.get(dst, set()) if norm(x) != norm(before)}
            rev.setdefault(src, set()).add(before)
    return {lab: sorted(vs) for lab, vs in rev.items() if set(vs) != set(orig_lab.get(lab, []))}


# override file -> its CSV header (a line list has none). Written when the override file is new.
_OV_HEADERS = {"fandoms.csv": "alias,canonical", "characters.csv": "variant,canonical,fandom",
               "genres_canon.csv": "variant,canonical", "tropes.csv": "variant,canonical,route"}


def synth_reject(key: str, kind: str, before: str, after: str, dest: str | None = None) -> tuple:
    """A wrangle reject -> how to suppress it. Returns (cls, actions, reason):
      cls='auto'   -> actions=[(override_file, line), ...] identity overrides to append.
      cls='manual' -> actions=[], reason=why it can't be an additive override (hand-edit).
    'auto' works because overrides load LAST and an identity map (X→X) is a no-op (verified against
    load_maps): re-pointing X to itself cancels the fold the built-in maps produced. A suppressed
    genre canon can leave X non-allowlisted (→ route to tags), so genres also allowlist X."""
    if kind == "rename":
        if key == "fandoms":    return "auto", [("fandoms.csv", f"{before},{before}")], ""
        if key == "characters": return "auto", [("characters.csv", f"{before},{before},")], ""
        if key == "genres":     return "auto", [("genres_canon.csv", f"{before},{before}"),
                                                 ("genres_allow.txt", before)], ""
        if key == "tags":       return "auto", [("tropes.csv", f"{before},{before},tag")], ""
    if kind == "move" and key == "genres" and dest == "tags":
        return "auto", [("genres_allow.txt", before)], ""
    reason = {"drop": "junk-drop or redundancy-strip — remove the junk.txt rule (or keep by hand)",
              "add": "an injected value (decompose/trope route) — remove the rule that adds it",
              "move": "cross-column move (character rescue / blocklist / decompose) — hand-edit the source map",
              "rename": "rename with no additive inverse — hand-edit the source map"}.get(kind, "hand-edit the map")
    return "manual", [], reason


def _reject_row(lab2key: dict, b: int, title: str, kind: str, where: str, before: str, after: str) -> dict:
    """One rejects.csv row for a wrangle reject (class computed by the shared synth_reject)."""
    if "→" in where:
        src, dst = [x.strip() for x in where.split("→")]
        key, dest = lab2key.get(src, src), lab2key.get(dst, dst)
        col = f"{key} → {dest}"
    else:
        key = lab2key.get(where, where); dest = None; col = key
    cls, _, _ = synth_reject(key, kind, before, after, dest)
    return {"stage": "wrangle", "book": b, "title": title, "kind": kind,
            "column": col, "before": before, "after": after, "class": cls}


def _step_walk(m: dict, beh: dict, cols: dict, perbook: dict, changes: dict,
               unique: dict, known_chars, tagcanon: dict) -> list:
    """Interactive 1-by-1 review of the per-book UNIQUE edits. Mass folds are already baked into
    `changes` and never shown. Mutates `changes` in place (revert-rejected-from-full-result) and
    returns the rejects to log. rich-only — the caller guards with ui.interactive()."""
    from scourgify import ui
    from scourgify.common import titles as book_titles
    from scourgify.wrangle import transform            # lazy: wrangle imports this module at top
    lab2key = {v: k for k, v in cols.items()}
    ids = sorted(unique, reverse=True)                         # newest ids first
    titles = book_titles(ro_connect(), ids) if ids else {}     # large sets fetch all (IN() cap lives in common)
    rejects = []
    for pos, b in enumerate(ids):
        edits = unique[b]
        title = str(titles.get(b, ""))
        items = [_edit_label(*e) for e in edits]
        acc, rej, action = ui.checklist(f"[bold]#{b}[/]  {title[:64]}", items, subtitle=f"book {pos + 1}/{len(ids)}")
        if action == "quit":                                   # leave this + all remaining un-walked books untouched
            for bb in ids[pos:]:
                for lab in cols.values(): changes.get(lab, {}).pop(bb, None)
            break
        if action == "skip":                                   # defer the whole book (reappears next run); NOT a reject
            for lab in cols.values(): changes.get(lab, {}).pop(b, None)
            continue
        if not rej: continue                                   # accepted all — `changes` already correct
        rej_edits = [edits[i] for i in rej]                    # only an explicit untick-then-apply is a declared reject
        d = {k: perbook[b].get(k, []) for k in cols}
        nd, _, _ = transform(d, m, beh, known_chars, tagcanon)
        nd_lab = {lab: nd.get(k, []) for k, lab in cols.items()}
        orig_lab = {lab: d.get(k, []) for k, lab in cols.items()}
        net = reconstruct(nd_lab, orig_lab, rej_edits)
        for lab in cols.values():
            if lab in net: changes.setdefault(lab, {})[b] = net[lab]
            else: changes.get(lab, {}).pop(b, None)
        rejects += [_reject_row(lab2key, b, title, *e) for e in rej_edits]
    return rejects


# ---------------- overrides: logged wrangle rejects -> override rules ----------------
def _append_override(path: str, lines: list) -> list:
    """Append lines to an override CSV (with header if new) / .txt list, skipping ones already present.
    -> the lines actually added."""
    fn = os.path.basename(path)
    existing, new = set(), not os.path.exists(path)
    if not new:
        existing = {l.strip() for l in read_lines(path)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    added = []
    with open(path, "a", newline="") as f:
        if new and fn in _OV_HEADERS:
            f.write(_OV_HEADERS[fn] + "\n"); existing.add(_OV_HEADERS[fn])
        for ln in lines:
            if ln.strip() in existing: continue
            f.write(ln + "\n"); existing.add(ln.strip()); added.append(ln)
    return added


def build_overrides(do_apply: bool = False, master: bool = False, only: set | None = None) -> dict:
    """Read data/rejects.csv, turn the auto-suppressible wrangle rejects into identity-override lines
    (grouped by target file), and list the manual ones for hand-editing. Dry-run unless do_apply.

    `only`: a set of (filename, line) pairs to keep — the 1-by-1 review path, so a rule you don't
    want simply isn't written. Returns the {filename: [line, ...]} plan, which is what the wizard
    renders its checklist from (one computation, not two).""" 
    from scourgify.common import rejects_path
    if not os.path.exists(rejects_path()):
        print(f"no rejects logged yet ({os.path.basename(rejects_path())} not found — reject something in `apply --step` first)."); return
    rows = [r for r in read_csv(rejects_path()) if r.get("stage") == "wrangle"]
    if not rows:
        print("no wrangle rejects to act on (classify rejects are AI hallucinations — log-only)."); return
    seen, auto, manual = set(), collections.defaultdict(list), []
    for r in rows:
        key = (r["kind"], r["column"], r["before"], r["after"])
        if key in seen: continue
        seen.add(key)
        col = r["column"]; src, dest = (col.split("→") + [None])[:2]
        src = src.strip(); dest = dest.strip() if dest else None
        cls, actions, reason = synth_reject(src, r["kind"], r["before"], r["after"], dest)
        if cls == "auto":
            for fn, line in actions: auto[fn].append(line)
        else:
            manual.append((r["kind"], col, r["before"], r["after"], reason))
    if only is not None:                       # 1-by-1 review kept only these lines
        auto = {fn: [l for l in lines if (fn, l) in only] for fn in auto}
        auto = {fn: lines for fn, lines in auto.items() if lines}
    tgt = DEF if master else overrides_dir()
    where = "defaults/ (MASTER — checkout only; installed defaults are read-only)" if master else "overrides/"
    print(f"{'APPLY' if do_apply else 'DRY-RUN'} — {sum(len(v) for v in auto.values())} auto-suppressible line(s) → {where}")
    total_added = 0
    for fn in sorted(auto):
        lines = sorted(set(auto[fn]))
        print(f"\n  {fn}")
        for ln in lines: print(f"      {ln}")
        if do_apply:
            added = _append_override(os.path.join(tgt, fn), lines)
            total_added += len(added)
            print(f"    → appended {len(added)} new (skipped {len(lines) - len(added)} already present)")
    if manual:
        print(f"\n  MANUAL — {len(manual)} reject(s) with no additive override (hand-edit; kept in the log):")
        for kind, col, before, after, reason in manual:
            v = f"{before} → {after}" if after else before
            print(f"      [{kind}] {col}: {v}\n          {reason}")
    if not auto and not manual:
        print("  (nothing to do)")
    if do_apply and auto:
        _archive_consumed_rejects()
        print(f"\napplied {total_added} override line(s); consumed auto rejects archived out of the log.")
    elif not do_apply and auto:
        print("\n(dry-run — re-run `scourgify overrides --apply` to write these"
              + (" to the master defaults" if master else "") + ".)")
    return dict(auto)


def _archive_consumed_rejects() -> None:
    """Move the auto (now-suppressed) wrangle rejects out of rejects.csv into a timestamped archive;
    keep manual wrangle rows and all classify rows (still actionable / informational)."""
    from scourgify.common import rejects_path, REJECT_COLS
    rows = read_csv(rejects_path())
    keep = [r for r in rows if not (r.get("stage") == "wrangle" and r.get("class") == "auto")]
    gone = [r for r in rows if r.get("stage") == "wrangle" and r.get("class") == "auto"]
    if gone:
        arch = rejects_path().replace(".csv", f"_applied_{time.strftime('%Y%m%d-%H%M%S')}.csv")
        with open(arch, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=REJECT_COLS, extrasaction="ignore"); w.writeheader(); w.writerows(gone)
    with open(rejects_path(), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REJECT_COLS, extrasaction="ignore"); w.writeheader(); w.writerows(keep)


def overrides_cmd(argv: list) -> None:
    import argparse
    p = argparse.ArgumentParser(prog="scourgify overrides",
        description="Turn logged wrangle rejects (from `apply --step`) into override rules so the same "
                    "wrong change never recurs. Dry-run until --apply; only appends to override files.")
    p.add_argument("--apply", action="store_true", help="write the override lines (default: preview only)")
    p.add_argument("--master", action="store_true", help="target bundled defaults/ instead of overrides/ (maintainer; checkout only)")
    a = p.parse_args(argv)
    build_overrides(a.apply, a.master)
