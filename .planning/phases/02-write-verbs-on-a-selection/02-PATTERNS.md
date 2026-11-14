# Phase 2: Write verbs on a selection - Pattern Map

**Mapped:** 2026-09-09
**Files analyzed:** 10 (3 new plugin modules, 6 modified core modules, `plugin/action.py`, `plugin/selftest.py`, `tests/test_plugin_source.py`)
**Analogs found:** 10 / 10 (every new/modified file has a same-repo analog already read this session)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| NEW `plugin/jobs.py` | controller (job body) | request-response (worker-thread PLAN/EXECUTE) | `plugin/action.py`'s `job_inspect`/`job_db_smoke`/`_open` (moved, not rewritten) | exact |
| NEW `plugin/picker.py` | component (Qt dialog) | request-response (renders plain data, returns decide-shaped tuples) | `plugin/config.py` settings widget (Qt-lazy-import shape) + `src/scourgify/ui.py::checklist` (semantics it mirrors) | role-match |
| NEW `plugin/result_dialog.py` | component (Qt dialog) | request-response (renders `WriteResult` + failures) | `plugin/picker.py` (same row widget, reused) / `plugin/action.py::_done`'s `info_dialog` (the thing it replaces) | role-match |
| MODIFY `plugin/action.py` | controller (Qt shell) | request-response (dispatch only, no data compute) | itself — `_run`, `_verb`, `build_menu`, `_done` are the templates every new dispatch/menu edit copies | exact |
| MODIFY `src/scourgify/wrangle.py` (`Plan.write`, `Plan.step`) | service (write producer / review) | CRUD (write), request-response (review) | itself — the `write=`/`decide=` seam already exists elsewhere in the same file's sibling functions (see below) | exact |
| MODIFY `src/scourgify/staleness.py` (`write`, `step`) | service | CRUD (write) | `wrangle.Plan.write` for the seam shape; `staleness.step` already has `decide=` — no analog needed | exact |
| MODIFY `src/scourgify/classify.py` (`apply_proposal`) | service | CRUD (write), event-driven (engine calls upstream) | `promote.backfill` (`run_writer(...)` call site + `decide=` precedent) | exact |
| MODIFY `src/scourgify/promote.py` (`backfill`, `run`'s `ask`/`verify_ask` default) | service | CRUD (write) / event-driven (LLM ask) | `classify.Plan.run`'s `ask=`/`(text, err)` envelope (the shape #73 says promote must adopt) | exact |
| MODIFY `src/scourgify/synopsis.py` (`Plan.run`) | service | CRUD (write), event-driven (LLM ask) | `classify.Plan.run` (same `ask=` seam) + `wrangle.Plan.write` (same `write=` seam) | role-match |
| MODIFY `src/scourgify/setup.py` (column/pref write) | service | CRUD (write) | `wrangle.Plan.write` (`write=` seam shape) | role-match |
| MODIFY `tests/test_plugin_source.py` | test | transform (AST source-grep) | itself — `MODULES`/`QT_MODULES` lists and the four existing assertions are what extend | exact |
| NEW `tests/test_plugin_jobs.py` | test | request-response (Qt-free unit test of `jobs.py`) | `tests/test_write_path.py` (shadow-replay pattern) + `tests/fixture_db.py` (the fixture every core-module test uses) | exact |

## Pattern Assignments

### `plugin/jobs.py` (NEW — controller, request-response)

**Analog:** `plugin/action.py` lines 40-205 (the existing `job_*` functions and `_open`/`_rejects_by_book`/`_archives_by_book` helpers) — this is a **relocation**, not a fresh pattern. D-12 moves everything below action.py's "job functions (worker thread)" banner comment into this new Qt-free file.

**Module docstring style to copy** (action.py lines 1-21):
```python
#!/usr/bin/env python3
"""The Qt layer: a toolbar button whose menu IS the current selection.

Deliberately thin, and kept thin BY ENFORCEMENT (tests/test_plugin_source.py, the same mechanism
tests/test_cli.py uses on wizard.py). Four invariants that file will fail on:
  1. `run_writer(` appears nowhere...
  ...
"""
```
`jobs.py`'s docstring should state its own invariant analogously: "Qt-free. No `qt.core`/`calibre.gui2` import anywhere in this file — that is what makes it importable and callable under plain CI Python with `tests/fixture_db.py`, with no Calibre and no GUI (D-12)."

**Job function signature to copy exactly** (action.py lines 62, 173):
```python
def job_inspect(lib_path, lib_uuid, ids, now_uuid=None, abort=None, log=None, notifications=None):
    ...
def job_db_smoke(lib_path, lib_uuid, api, now_uuid=None, abort=None, log=None, notifications=None):
```
Every new PLAN/EXECUTE job in `jobs.py` (`job_plan_wrangle`, `job_execute_wrangle`, `job_plan_classify`, `job_execute_classify`, etc.) keeps this positional-then-keyword-abort/log/notifications shape — `test_plugin_source.py`'s job-signature assertion depends on it.

**The `_open` bind pattern to copy verbatim** (action.py lines 40-59):
```python
def _open(lib_path, lib_uuid, now_uuid=None):
    from scourgify import common
    common.set_library(lib_path)
    if lib_uuid and now_uuid is not None:
        try: current = now_uuid()
        except Exception: current = None
        if current and current != lib_uuid:
            return None, ('The library changed since you clicked, so nothing was read.\n'
                          'Open the scourgify menu again.')
    return common.ro_connect(), None
```
For an EXECUTE job, the equivalent bind resolves `api` from an argument the action passes in (mirroring `job_db_smoke`'s `(lib, uuid, self.gui.current_db.new_api, self.current_uuid)` tuple) — never a fresh `ro_connect()` for a write.

**Progress/abort pattern to copy** (action.py lines 89-94, notifications takes ONE tuple — Pitfall 2):
```python
for n, b in enumerate(ids):
    if abort is not None and abort.is_set():
        break
    if notifications is not None:
        notifications.put((n / max(len(ids), 1), 'reading %d of %d' % (n + 1, len(ids))))
```

**Result dict shape to copy** — every job returns a plain dict, never an object (action.py line 73, 122, 146, 182, 202): `{'title': ..., 'msg': ..., 'det': ...}` for read jobs. PLAN/EXECUTE jobs in `jobs.py` need their own dict keys (`summary`, `items`, `ops`, `consequence`, `write_result`, `engine_failures` per Pitfall 7) — same "plain dict, no custom class" discipline, no Qt object ever crosses the Dispatcher boundary.

**Error-to-clean-result conversion (D-11) — pattern to add, no existing analog in this repo yet.** Model it on the `_open` error-tuple return shape above: catch `GuardrailError` around the EXECUTE job's `write=`/`ask=` calls and return `{'title': ..., 'msg': str(err), 'det': '', 'refused': True}` rather than letting it propagate — `job_inspect`'s `if err: return {...}` early-return is the existing precedent for "errors become a result dict, not an exception," just not yet applied to a `GuardrailError` from a write path.

---

### `plugin/picker.py` (NEW — component, request-response)

**Analog 1 — Qt-lazy-import shape:** `plugin/config.py` (widget imported INSIDE `config_widget()`, never at module level, so Qt stays out of command-line use — same discipline `picker.py` needs since it's imported by `action.py` which is itself Qt-importing at module level, but the new file should still avoid importing `scourgify.ui`).

**Analog 2 — the review semantics it mirrors:** `src/scourgify/ui.py::checklist` — read its full signature and return shape before building the picker's "Review 1-by-1" control, since D-02 says the picker's tick-list must produce the SAME `(accepted_idx, rejected_idx, action)` shape that feeds every `decide=` site.

```bash
grep -n "def checklist" -A 30 src/scourgify/ui.py
```
(Not re-quoted here — planner should read `src/scourgify/ui.py:100-130` directly; `ask_retry`'s return shape at `engines.py:320` is a related two-value pattern: `(text, err)`.)

**The dispatch pattern the picker's Run button uses to launch EXECUTE** (action.py lines 302-312, the ONLY `_run` call shape, reused verbatim, not reinvented):
```python
def _run(self, description, func, args, done=None):
    """`done` lets a caller own its own completion..."""
    t0 = time.monotonic()
    job = ThreadedJob('scourgify', description, func, args, {},
                      Dispatcher(done or self._done))
    self.gui.job_manager.run_threaded_job(job)
```
The picker never constructs a `ThreadedJob` itself — it calls back into `action.py`'s `_run` (or a thin wrapper on the action) with `done=self.on_execute_done`.

---

### `plugin/result_dialog.py` (NEW — component, request-response)

**Analog:** `plugin/action.py::_done` (lines 314-321) is the dialog it REPLACES for write verbs (read-only verbs keep using `info_dialog` via `_done`):
```python
def _done(self, job):
    if job.failed:
        return self.gui.job_exception(job, dialog_title='scourgify failed')
    r = job.result or {}
    info_dialog(self.gui, r.get('title', 'scourgify'), r.get('msg', ''),
                det_msg=r.get('det', ''), show=True)
```
`result_dialog.py`'s `show(job)` function copies this `job.failed` check (still needed for a REAL exception per D-11 — only `GuardrailError` is pre-converted to a clean result inside the job, everything else still reaches `job_exception`), then renders `job.result`'s `write_result`/`engine_failures` keys instead of a plain `msg`/`det` string pair. It reuses the picker's row widget (book/field/before/after/state) rather than building a second one.

**Refresh call to copy** (Code Examples, RESEARCH.md — cross-checked against Calibre's public source, no in-repo analog since this repo has never called it yet):
```python
self.gui.library_view.model().refresh_ids(list(touched_book_ids))
```
Call this from the Dispatcher-wrapped `done` callback, same thread as the dialog show.

---

### `plugin/action.py` (MODIFY — controller, request-response)

**`_verb` — the exact greying pattern every new live verb slot must keep using** (lines 271-283):
```python
def _verb(self, menu, label, tag, disabled_reason, slot=None, hint=''):
    """One fixed slot. A verb that does not apply greys out WITH ITS REASON rather than
    vanishing, so a slot never comes to mean something else (B1 edge case)."""
    text = '%s — %s' % (label, tag)
    if disabled_reason:
        text += ' (%s)' % disabled_reason
    a = menu.addAction(text)
    a.setToolTip(hint or disabled_reason or '')
    if slot is None or disabled_reason:
        a.setEnabled(False)
    else:
        a.triggered.connect(lambda checked=False: slot())
    return a
```
Turning `SOON` into a live verb means replacing the `SOON` disabled_reason argument with `None` (or a computed lock-state reason string, per Pattern 6) and passing a real `slot=lambda: self.wrangle(scope)`-style bound method — exactly how `head`/`Inspect`/`db_smoke` already do it (lines 250-268, 292-300).

**Lock-greying state to add** — no in-repo analog (new for this phase); model the state dict on the existing `scope` capture pattern at line 240 (`build_menu` reads plain instance/module state, never a core call): a `self._write_running: dict[str, bool]` set right before `self._run(...)` for a write verb, read (not written) inside `build_menu`, and cleared inside the wrapped `done`.

**D-14's import-rule tightening target** — today ONLY `_open` at line 40 uses the "private-helper" exemption inside `action.py`; after D-12 moves the `job_*` bodies (and `_open`/`_rejects_by_book`/`_archives_by_book`) to `jobs.py`, `action.py` should have ZERO core imports outside function scope at all — the planner's task should assert `tests/test_plugin_source.py`'s tightened rule (`func.startswith("_")` exemption removed) passes cleanly against the post-split `action.py`.

---

### `src/scourgify/wrangle.py::Plan.write` / `Plan.step` (MODIFY — service, CRUD/request-response)

**Current signature and call site** (lines 423-437):
```python
def write(self, force: bool = False) -> None:
    ops = []
    for lab, ch in self.changes.items():
        k = next(key for key, label in self.cols.items() if label == lab)
        expected = {b: sorted(self.perbook[b].get(k, [])) for b in ch}
        ops.append(op_set_field(lab, ch, expected=expected))
    run_writer(ops, force=force, tool="wrangle", scope=self.scope)
```
**Target shape** (Pattern 1, RESEARCH.md): add a keyword-only `write=run_writer` parameter and call `write(ops, force=force, tool="wrangle", scope=self.scope)` instead of the hardcoded name — default argument preserves CLI behaviour byte-for-byte. Precedent for "keyword-only callable, default preserves old behaviour" is `Plan.run(ask=None)` (classify.py:396) and `promote.backfill(decide=)` (promote.py:365).

**`Plan.step` — current signature, NO `decide=` (the one gap Pattern 2 names)** (lines 405-421):
```python
def step(self) -> None:
    """1-by-1 review of the per-book UNIQUE edits (ui.checklist); rejected edits are removed
    from this plan's changes and logged for `scourgify overrides`."""
    from scourgify import ui
    if not ui.interactive():
        raise GuardrailError("--step needs an interactive terminal (omit it for a bulk apply).")
    _, unique = _classify_edits(self.m, self.diffs)
    if not unique: return
    from scourgify.overrides import _step_walk   # lazy: breaks the wrangle<->overrides import cycle
    rejects = _step_walk(self.m, self.beh, self.cols, self.perbook, self.changes, unique,
                         self.known_chars, self.tagcanon)
    ...
```
Copy the `staleness.step(status_label, rows, decide=None)` signature shape (staleness.py:64) — add `decide=None` to `Plan.step`, thread it to `_step_walk`'s own `decide=` parameter (`overrides.py:166` — `_step_walk` already accepts `decide=None`, it is only `Plan.step` that doesn't pass it through). This closes the ONLY remaining `decide=` gap; do not add a `decide=` to `overrides._step_walk` itself — it already has one.

---

### `src/scourgify/staleness.py::write` (MODIFY — service, CRUD)

**Current call site** (line 83-89):
```python
def write(status_label: str, rows: list) -> None:
    ...
    run_writer([op_set_field(status_label, {b: n for b, o, n, _ in rows},
               expected=...)], tool="staleness", scope=f"{len(rows)} books")
```
Same `write=run_writer` keyword-only addition as `wrangle.Plan.write`. `staleness.step` already has `decide=None` — no change needed there.

---

### `src/scourgify/classify.py::apply_proposal` (MODIFY — service, CRUD)

**Current signature and call site** (line 226, 272):
```python
def apply_proposal(rows: list | None = None) -> None:
    ...
    run_writer(ops, tool="classify", scope=f"{len(processed)} books")
```
**Target:** add `write=run_writer, engine=None, model=None` keyword-only params, thread `engine=`/`model=` into the `run_writer(...)` call (D-05's "edit-log header carries engine+model"). The comment already anticipates this at classify.py:269-271 — quote it in the plan task: *"engine/model stay unset here: --apply is its own invocation ... The plugin's classify verb runs the pass and the write in one job and passes both (write_ops(engine=…, model=…))."*

**Pitfall 3 guard to add here specifically:** before appending `op_create_column(...)` for `#wrangled`'s first-run case, check `custom_column_id(con, "wrangled") is not None` and raise `GuardrailError` (not letting a bare `ValueError` reach `ops.apply_ops`) when the plugin (in-process, no legacy-DB reopen available) is the caller.

---

### `src/scourgify/promote.py::backfill` / `run`'s `ask`/`verify_ask` default (MODIFY — service, CRUD/event-driven)

**Current `backfill` write call site** (line 403):
```python
run_writer([op_set_field("tags", chg, expected=expected)], tool="promote", scope=f"backfill, {len(chg)} books")
```
Same `write=run_writer` addition as wrangle/staleness.

**Current `ask`/`verify_ask` default — the #73 gap** (lines 408-427):
```python
def run(a: argparse.Namespace, ranked_path=None, proposal_path=None, review_path=None,
        existing=None, ask=None, verify_ask=None) -> None:
    """ask/verify_ask: prompt -> response text. Default to the configured engines; tests pass
    callables directly (the same seam decide() already has) instead of faking the registry."""
    ...
    if ask is None:
        eng = ENGINES[a.engine](a.model, a.timeout)
        ask = lambda p: ask_retry(eng, p)[0]          # <-- discards err; bare string only
    if verify_ask is None and a.verify_with:
        veng = ENGINES[a.verify_with]("", a.timeout)
        verify_ask = lambda p: ask_retry(veng, p)[0]   # <-- same gap
```
**Analog for the target shape:** `classify.Plan.run` / `synopsis.Plan.run`'s own default-`ask` branch (classify.py:408-410, synopsis.py:226-228) — both build `ask = lambda prompt: ask_retry(eng, prompt)` returning the FULL `(text, err)` tuple, not `[0]`-sliced. #73 requires `promote.run`'s default `ask`/`verify_ask` to stop slicing, AND `promote.decide()` (promote.py:118-137) to unpack `(text, err)` and prefix the ledger `reason` with `engines.failure_class(err)` the way `classify.py`'s call sites already do (see `engines.py:320-336`, esp. the `ask_retry` docstring on how the class-prefix convention is produced).

---

### `src/scourgify/synopsis.py::Plan.run` (MODIFY — service, CRUD/event-driven)

**Current monolithic call site** (line 218-268, write at 268):
```python
def run(self, ask=None) -> None:
    ...
    run_writer(ops, tool="synopsis", scope=f"{len(made)} written, {len(kept)} kept")
```
Same `write=run_writer` addition. Engine construction gap is Pattern 3 — the plugin's EXECUTE job must always pass `ask=` built via `engines.resolve_keys(...)`, never rely on `Plan.run(ask=None)`'s default branch (synopsis.py:226-228 mirrors classify's `ENGINES[a.engine](a.model, a.timeout)` — no `env=`, exactly the gap Pattern 3 describes).

**Open Question #1 (unresolved by CONTEXT.md):** whether `Plan.run` also grows a `decide=` threaded to its internal `step(made, self.titles)` call (line ~250) for D-13's per-book synopsis review, or whether the plugin skips per-book review and relies on diff-after — RESEARCH.md recommends classify's shape (no pre-write review) as default; CONTEXT.md's D-13 (added 2026-09-09, AFTER RESEARCH.md's Open Question was written) has since SETTLED this: **synopsis DOES get a per-book review tick-list before the write** — so `Plan.run` needs `decide=None` threaded to its internal `step()` call after all. Flag this explicitly to the planner: RESEARCH.md's Pattern 5 / Open Question #1 predates D-13 and is superseded by it.

---

### `src/scourgify/setup.py` (MODIFY — service, CRUD)

**Current call site** (line 184): `run_writer(ops, tool="setup", scope="columns + prefs")`. Same `write=run_writer` keyword-only addition, for parity — though CLAUDE.md notes column creation stays Phase-3/CLI-only (`create_column` is out of `common.write_ops`'s in-process contract per `ops.py:56-60`), so this seam may be lower-priority / defer-eligible; confirm scope with the planner rather than assuming it must land this phase.

---

## Shared Patterns

### The `write=` injection seam (all six write-producing functions)
**Source:** precedent already established by `Plan.run(ask=None)` (`src/scourgify/classify.py:396`) and `promote.backfill(decide=)` (`src/scourgify/promote.py:365`)
**Apply to:** `wrangle.py::Plan.write`, `staleness.py::write`, `classify.py::apply_proposal`, `promote.py::backfill`, `synopsis.py::Plan.run`, `setup.py`'s write call
```python
# illustrative target shape (not existing code):
def write(self, force: bool = False, write=run_writer) -> None:
    ...
    write(ops, force=force, tool="wrangle", scope=self.scope)
```
The EXECUTE job binds a `write_ops`-wrapped callable:
```python
def _plugin_write(api, engine=None, model=None):
    def write(ops, force=False, tool="plugin", scope=None):
        return common.write_ops(api, ops, force=force, tool=tool, scope=scope,
                                engine=engine, model=model)
    return write
```
`tests/test_plugin_source.py` must keep failing on any literal `run_writer(` inside `plugin/*.py` — the seam exists precisely so no plugin file ever needs to call it.

### The `decide=` seam (six of seven sites already built; one gap)
**Already built** (Phase 1, all defaulting to `ui.checklist`, lazy-imported behind `decide is None`):
| Function | Signature | File:line |
|---|---|---|
| `staleness.step` | `step(status_label, rows, decide=None)` | staleness.py:64 |
| `synopsis.step` | `step(made, titles, decide=None)` | synopsis.py:287 |
| `promote.apply_decisions_step` | `apply_decisions_step(review_path=None, decide=None)` | promote.py:192 |
| `promote.backfill_step` | `backfill_step(chg, adds, titles, decide=None)` | promote.py:347 |
| `promote.backfill` | `backfill(yes=False, step=False, decide=None)` — DIFFERENT shape: `decide(chg, adds) -> chg` | promote.py:365 |
| `classify.apply_proposal_step` | `apply_proposal_step(decide=None)` | classify.py:280 |
| `overrides._step_walk` / `overrides.step_pick` | both `decide=None` | overrides.py:166, 231 |

**Still missing:** `wrangle.Plan.step()` (wrangle.py:405-421) — always calls `_step_walk(...)` with no `decide=` passed through. Add `def step(self, decide=None) -> None` and pass `decide=decide` into the `_step_walk(...)` call.

**Checklist `decide` shape** (`ui.checklist`'s own return contract, `src/scourgify/ui.py:121-125`): `decide(title, items, subtitle="") -> (accepted_idx, rejected_idx, action)`, `action ∈ {'apply','skip','quit'}`. `promote.backfill`'s `decide` is a DIFFERENT shape (`decide(chg, adds) -> chg_to_write`, falsy aborts) — the picker needs two adapters, not one generic one.

### Engine construction — always inject `env=`, never call `Plan.run(ask=None)` from the plugin
**Source:** `plugin/config.py::job_verify`, lines 49-69 (existing, unmodified) — THE template for every engine construction this phase adds:
```python
def job_verify(engine, key, abort=None, log=None, notifications=None):
    from scourgify import engines
    if engine not in engines.ENGINE_ENV:
        return {'engine': engine, 'ok': False, 'cls': '', 'detail': 'on-device — nothing to verify'}
    try:
        eng = engines.ENGINES[engine]('', 30, env={engines.ENGINE_ENV[engine][0]: key})
    except Exception as e:
        return {'engine': engine, 'ok': False, 'cls': engines.AUTH,
                'detail': engines.redact('%s' % e, key)}
    out, reason = engines.ask_retry(eng, 'Reply with the single word: ok', tries=1)
    return {'engine': engine, 'ok': bool(out and not reason), 'cls': engines.failure_class(reason),
            'detail': reason or (out or '').strip()[:80]}
```
**Apply to:** every classify/promote/synopsis EXECUTE job in `jobs.py`. Never call `Plan.run()`/`promote.run()` with `ask=None` — always:
```python
env = engines.resolve_keys(plugin_config.stored_keys())
eng = engines.ENGINES[engine_id](model, timeout, env=env)
ask = lambda prompt: engines.ask_retry(eng, prompt)   # (text, err) tuple — classify/synopsis shape
```

### Engine picker data (D-07) — already-built pure functions, don't re-derive
**Source:** `src/scourgify/engines.py`
- `TRAITS` (`engines.py:40+`, per-engine dict: `parallel`, `judge`, `hint`, `unusable`, `out_tokens`, `refuses`, `role`, `limits`, `platforms`) — `_TRAIT_DEFAULTS` at line 34 shows every key's default.
- `PRICING = {"apple": (0.0, 0.0), "claude": (1.00, 5.00), "openai": (0.15, 0.60), "gemini": (0.30, 2.50), "mistral": (0.20, 0.60)}` (line 19) — $/MTok (input, output).
- `usable_engines(env=None) -> list` (line 219).
- `engine_rows(env=None) -> list` (line 240) — **already exists**, exactly the helper D-07 references: `[(name, usable, hint)]`, derived from `ENGINES + usable_engines() + TRAITS`.
- `default_engine_id(opts, judge=False, usable=None) -> str` (line 249).
- `engine_options(engs, n_todo, cost_fn) -> list` (line 261) — numbered rows with per-engine cost, `cost_fn(n_books, engine)` injected (`classify.est_cost` in production).
- `classify_error(exc) -> str` (line 285), `failure_class(reason) -> str` (line 302), `redact(msg, *secrets) -> str` (line 310), `ask_retry(eng, prompt, tries=4) -> tuple` (line 320), `mask`/`unmask` (lines 204, 211).

The picker's engine buttons render `engine_rows`/`engine_options` output verbatim — no hand-rolled cost string, no hand-rolled failure-mode sentence.

### The one `_run` dispatch site — no new module builds a `ThreadedJob`
**Source:** `plugin/action.py:302-312` (quoted above under `plugin/picker.py`). Every new dispatch (PLAN, EXECUTE, retry) routes through this one method, passing `done=` for caller-owned completion, exactly as `plugin/config.py:176-177`'s `verify()` already does.

### `notifications.put` takes ONE tuple, not two positional args (Pitfall 2)
**Source:** `plugin/action.py:94` — `notifications.put((n / max(len(ids), 1), 'reading %d of %d' % (n + 1, len(ids))))`. D-08's phrasing in CONTEXT.md reads as two positional args; the working code is one tuple. Every new progress-reporting job copies this exact call shape.

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| Lock-greying state on `action.py` (`self._write_running`) | state/controller | event-driven | New for this phase — no prior in-process "a write is running" Qt-side flag exists; model loosely on the `scope` capture at `build_menu` line 240 (plain instance state, no core call), per CONTEXT.md's own discretion guidance rather than an existing analog. |
| `GuardrailError` → clean result-dict conversion at the job-wrapper boundary (D-11) | error handling | request-response | No write job has existed in this repo's plugin before this phase; `job_inspect`'s `if err: return {...}` (action.py:72-73) is the closest shape but that's an `_open`-identity error, not a `GuardrailError` from a write/engine call — treat as a new pattern modeled on that shape, not a literal copy. |
| Pitfall 3/5 pre-flight guard (`custom_column_id(...) is not None` check before ever letting an in-process write emit `op_create_column`) | guard | request-response | No existing call site does this check today — `apply_proposal`'s `have_wrangled` gate and `Plan.run`'s `have_stamp` gate exist for the CLI's legacy-DB path; the plugin needs a NEW explicit refusal converting a would-be `ValueError` into a `GuardrailError` before `write_ops` is ever called. |

## Metadata

**Analog search scope:** `plugin/*.py`, `src/scourgify/{wrangle,staleness,classify,promote,synopsis,setup,engines,common,ops,ui,overrides}.py`, `tests/test_plugin_source.py`, `tests/test_write_path.py`
**Files scanned:** ~14 (all named in the required-reading + `<code_context>`/`<canonical_refs>` blocks of 02-CONTEXT.md and 02-RESEARCH.md's Sources section)
**Pattern extraction date:** 2026-09-09
