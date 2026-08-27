# Pitfalls Research

**Domain:** Cross-platform Calibre GUI plugin — writes to a live library from `ThreadedJob`
workers, calls paid LLM APIs, distributed via MobileRead/GitHub
**Researched:** 2026-08-28
**Confidence:** HIGH for pitfalls sourced from this repo's own NLSpec/CONCERNS (measured,
first-party, already-happened-once evidence); MEDIUM for externally-sourced Calibre-ecosystem
pitfalls (cross-checked against official docs + MobileRead/GitHub threads); LOW is flagged
inline where the only evidence is a single web source with no second corroboration.

This research draws on two evidence classes deliberately kept distinguishable below:
- **[FIRST-PARTY]** — already caught in *this* codebase during phases 1–5 of the plugin build
  (recorded as dated amendments in the NLSpec and in `.planning/codebase/CONCERNS.md`). These
  are not hypothetical; they are bugs that were live in this repo and got fixed. The milestone
  is adding write actions, a dashboard, marks-based review, in-plugin column creation, Windows
  support and public distribution — territory adjacent to, but past, where phases 1–5 stopped,
  so these are the load-bearing precedent for what breaks next.
- **[ECOSYSTEM]** — sourced from Calibre's own plugin docs, MobileRead threads, and other
  shipped plugins (DeDRM, EpubMerge, kiwidude's plugins) via web research this session.

---

## Critical Pitfalls

### Pitfall 1: A guard that reads back its own write can never fire [FIRST-PARTY]

**What goes wrong:**
A safety check re-derives the value it's supposed to be validating from the same source the
write already touched, so the check always passes — the guard *looks* covered (a test
exercises the code path) but structurally cannot detect the failure it exists for.

**Why it happens:**
This project hit it exactly once already: B1's "validate library identity against
`gui.current_db` at job start" was first implemented by re-reading the uuid out of the db at
the **captured path** — which, after a library switch, still exists and still holds the same
uuid. The comparison was library-to-itself. It had to be corrected to call back into the live
action for `gui.current_db.new_api.library_id` — a plain attribute read from the worker, not a
re-derivation from the captured path. The bug was found by writing an adversarial test (a
selftest that dispatches with a uuid that was never this library's) — the same branch a real
switch takes.

**How to avoid:**
For every new guard added in this milestone (marks-review snapshot/restore identity check,
write-lock ownership check, dashboard per-library state binding), ask explicitly: "does this
comparison reach a value independent of the thing being validated?" A guard that reads
`gui.current_db` directly, not a path captured earlier, is the pattern that worked.

**Warning signs:**
A guard test only exercises the happy path (never switches library / never triggers the
failure mode mid-flight) — code coverage without behavior coverage. Any guard whose two sides
are read from the same variable/closure captured at the same time.

**Phase to address:**
Every phase that adds a new library-identity or state-freshness check (dashboard per-library
binding, B1 selection capture reused for marks review, write-run lock ownership) — write the
adversarial branch (switch-mid-flight) into that phase's smoke test, not just the happy path.

---

### Pitfall 2: Resources bundled beside the code are silently empty inside a plugin zip [FIRST-PARTY]

**What goes wrong:**
`common.HERE` (or any `open()`/`os.path.exists()` call relative to the module's own directory)
resolves to a path *inside the zip*, which is not a real filesystem path. Under Calibre's
`Plugin.__enter__`, the zip is appended to `sys.path` and zipimport resolves `import`
statements fine — but a bare `open(HERE / "defaults" / "fandoms.csv")` fails, and worse, some
call sites were written to **return empty data on a missing path rather than raise** (a
defensive-looking pattern that is actually the trap): `wrangle.load_maps()` was building empty
maps silently; `classify._read_vocab_file` returns `[]` for a missing path *by design*, so
`classify.load_vocab()` returns an empty vocabulary and `classify.est_cost()` — which prices
`len(", ".join(load_vocab()))` — **silently under-quotes** every classify run inside the
plugin. This is the same failure shape as the flat-80 `out_tokens` estimate that under-quoted
Gemini by 5–10x (`CONCERNS.md`), and B4.3 makes the displayed cost the *only* gate on
irreversible spend — so under-quoting is the dangerous direction, not a cosmetic bug.

**Why it happens:**
Code written and tested against a checkout (`common.HERE` = a real directory) behaves
correctly for months of CLI use, and only fails once the exact same code runs from inside a
zip — which nothing in the CLI test suite exercises.

**How to avoid:**
The `DEFAULTS` resource seam (extract-once to a version-keyed cache dir, or `get_resources()`)
must land **before** any feature that reads bundled data ships in the plugin — this was
explicitly logged as a phase-6 blocker in the NLSpec, and the phase-5 amendment states it must
land **before the engine picker shows a cost**, not merely before a wrangle run, because the
engine picker's cost is exactly the code path that under-quotes. Any function that returns `[]`
or `{}` for "file not found" needs an explicit audit for whether "found nothing" and "path not
readable" are actually the same case — they are not inside a zip.

**Warning signs:**
Any `_read_*_file`/`load_*` function whose "not found" branch returns an empty collection
instead of raising. Any cost/estimate function whose inputs trace back to a bundled file read.
Grep for `common.HERE` and everywhere it feeds a path.

**Phase to address:**
The `DEFAULTS` resource seam is a hard blocker phase, ordered before both the in-plugin write
actions phase (#59) and any UI phase that renders a classify cost. Verification: a smoke test
that imports every core module *from inside a built plugin zip* (not just from the checkout)
and asserts `load_maps()`/`load_vocab()` return non-empty on a known-good library.

---

### Pitfall 3: Multi-library state bleeds across libraries via a global `user_dir()` [FIRST-PARTY]

**What goes wrong:**
`user_dir()` (and everything under it — proposals, failure log, edit log, config, backups) is
global to the machine, not scoped per Calibre library. The first read-only feature that landed
in phase 4 already tripped over this on a throwaway 6-book library: "Inspect" reported a book
as "applied from `classify_proposal_applied_20260726-…csv`" — an archive belonging to the
*real* production library, because book ids collide across separate libraries and nothing
disambiguates which library an artifact file belongs to. Confirmed reproduced again, unchanged,
in phase 5.

**Why it happens:**
The CLI predates multi-library plugin use entirely — it always operated against exactly one
`$CALIBRE_LIBRARY` per invocation, so `user_dir()` never needed a library dimension. The plugin
inherits every core module unmodified, so the same global path is now shared across however
many libraries a Calibre user has (Calibre's whole raison d'être is multi-library).

**How to avoid:**
Namespace the `user_dir()` tree by library uuid (already decided and scheduled — folded into
phase 6, "the first phase whose correctness depends on it," alongside the writer seam and the
`DEFAULTS` seam). Every artifact-reading feature from phase 6 on is wrong across two libraries
until this lands — treat it as a hard prerequisite, not an enhancement, for the marks-review
phase (#61) and the dashboard (#60), both of which read artifacts per-library.

**Warning signs:**
Any feature demoed against only one library "looking fine" — this bug is invisible with a
single library open, which is most manual testing. Test explicitly with two throwaway
libraries open in sequence and cross-check that library A's artifacts never render against
library B.

**Phase to address:**
Namespacing lands in the same phase as the writer seam and `DEFAULTS` seam (per the NLSpec's
2026-08-11 owner decision) — before B6 (dashboard) or B8 (review) ship, since both read
per-library artifact state. Verification: the exact throwaway-library repro from phase 4/5
(Inspect reporting a foreign archive filename) re-run and asserting it no longer happens.

---

### Pitfall 4: `create_column`'s legacy-DB reopen desyncs a live GUI — there is no safe in-process fix [FIRST-PARTY]

**What goes wrong:**
Custom column creation needs Calibre's legacy `DB(LIB).create_custom_column(...)` object, then
a **reopen** of `DB(LIB).new_api` before the new column is usable in the same process. Done
in-process against a GUI that already holds the old `new_api`/models open, this desyncs the
live GUI's caches from the actual schema — not a narrow bug, a structural mismatch between
"the API object the plugin just got" and "the API object every other part of Calibre's GUI is
still holding." This is why the NLSpec deliberately puts `create_column` **outside** the
in-process write contract (B2.6) and mandates a restart prompt (B6.5) rather than trying to
make it live.

**Why it happens:**
The reopen pattern comes from the CLI/`calibre-debug` world, where nothing else holds a
reference to the old `new_api` — reusing that exact pattern inside a running GUI process is the
trap, because *everything else in the GUI* still holds the old reference.

**How to avoid:**
Do not attempt a "live" column-creation path no matter how tempting it looks in testing (a
single throwaway library with nothing else open will not surface the desync — it needs a
realistic GUI with multiple panels/views bound to the old models). Keep the one-time restart
prompt as the only sanctioned flow; this is a deliberate, accepted exception to "no handoffs"
recorded in B6.5, not a gap to close later.

**Warning signs:**
Any code path that calls `DB(...).create_custom_column` followed by reassigning
`gui.current_db` or similar in the same running process without a full app restart. A demo
that "seems to work" on a fresh library with only the scourgify dashboard open — that is
exactly the condition that hides the desync.

**Phase to address:**
The in-plugin first-run setup phase (custom-column creation, milestone Active item). Verify by
opening a second, unrelated Calibre panel (e.g. Tag Browser, tag editor) before and after
column creation without a restart, and confirming it does NOT reflect the new column until
restart — the absence of a live update *is* the correct behavior here, and a test that expects
otherwise is testing for the wrong thing.

---

### Pitfall 5: Dynamic per-selection menus built without a library-switch guard crash Calibre [ECOSYSTEM]

**What goes wrong:**
Calibre plugins that build dynamic menu entries (e.g. per-selection or per-library-state menu
items, which scourgify's B1 selection-driven menu does by design) are a documented source of
crashes on library switch. The fix pattern used by at least one real MobileRead plugin
(EpubMerge) was drastic: remove the dynamic menu construction entirely rather than make it
switch-safe. Corroborating detail: this is not centrally documented in Calibre's official bug
tracker — it is tribal plugin-author knowledge, found via forum troubleshooting threads (the
standard advice for "Calibre crashes on library switch" is "disable/uninstall plugins one by
one"), which means scourgify cannot rely on Calibre's own docs to catch this — it needs its own
test.

**Why it happens:**
A menu built from `gui.library_view.get_selected_ids()` and the *current* db/context, if not
rebuilt or torn down correctly on `library_changed()`, can hold references into a db object
that is about to be closed as part of the switch — Qt objects surviving past their backing
data is the general Calibre-plugin crash shape.

**How to avoid:**
scourgify's B1 already captures the id list + library uuid *at click time* and validates
identity at job start (Pitfall 1's guard) — that mitigates the *job* side. The **menu
construction** side still needs its own switch-safety: rebuild/clear the menu on
`library_changed()` rather than assuming the previous build's state is safe to reuse, and never
hold a `new_api`/`db` reference inside a menu action's closure across a switch.

**Warning signs:**
Menu items built once in `genesis()` and mutated in place rather than rebuilt on
`library_changed()`. A closure inside a `QAction.triggered` handler that captures `api`/`db` by
reference instead of resolving it fresh at click time.

**Phase to address:**
B1's menu (already shipped, phase 4) should get an explicit regression test for
switch-during-open-menu as part of the multi-library work folded into phase 6; the dashboard
(B6) inherits the same risk for its stage buttons and needs the same rebuild-on-switch
discipline from the start rather than retrofitted later.

---

### Pitfall 6: `JSONConfig` chmod can fail silently, leaving keys world-readable [FIRST-PARTY]

**What goes wrong:**
`plugin/config.py` chmod's the settings file to 0600 after writing, wrapped in a bare
`except: pass` — documented in `CONCERNS.md` as a known gap. On systems with ACLs, unusual
permission models, or certain network-mounted config dirs (plausible on Windows via WSL, or a
`%APPDATA%` roaming profile on a network share in a corporate/managed environment), the chmod
raises and is silently swallowed — the key stays at whatever the default umask leaves it,
typically world-readable.

**Why it happens:**
`chmod` is a POSIX permission call; it is meaningful on macOS/Linux but has weaker/different
semantics on Windows NTFS ACLs, where `os.chmod` often only toggles a read-only bit and cannot
express "owner-only." Windows support is new to this milestone, so this code path — silently
tolerant of chmod failure — is about to run on a platform where the *concept* it's securing
barely applies the same way.

**How to avoid:**
Log (not swallow) a chmod failure and surface it once in the settings dialog ("could not
restrict file permissions on this system"). On Windows, treat chmod as best-effort by design
(NTFS ACLs are the real mechanism, and setting them portably from Python is a bigger job than
this milestone needs) but say so rather than implying protection that isn't there.

**Warning signs:**
No warning currently exists at all — silence is the bug. Any settings-widget review that
doesn't include "save a key on a locked-down/managed volume and check the resulting file mode."

**Phase to address:**
Windows support phase (cross-platform core), since this is precisely where the assumption
gets tested for the first time; also worth a one-line fix immediately regardless of platform
since it's a silent-failure anti-pattern independent of OS.

---

### Pitfall 7: `pgrep`/`ps`-based "is Calibre open" detection has no Windows equivalent — and fails closed if absent [ECOSYSTEM + FIRST-PARTY]

**What goes wrong:**
`common.calibre_open()` uses `pgrep` → `ps` to detect a running Calibre process for the CLI's
"refuse to write while Calibre is open" guard. Neither binary exists on Windows by default.
`CONCERNS.md` already documents that when *both* are missing, the guard **fails closed**
(assumes Calibre is open) with no override — which is the safe failure mode for the CLI write
path, but becomes a real usability problem the moment Windows support is added: every CLI write
on Windows could refuse unconditionally unless a Windows-native detection path is added.
Separately, this guard is **already known to be irrelevant in-process** (B2.3: `calibre_open()`
is "skipped by design" in-process, since there's no second process to protect against) — so the
in-plugin write path is unaffected, but the **CLI on Windows** (still a supported surface per
"CLI/wizard unchanged throughout") is not.

**Why it happens:**
`pgrep`/`ps` are Unix process-listing tools with no direct Windows CLI equivalent (Windows has
`tasklist`/`wmic process`/`psutil` as alternatives, none of which are stdlib).

**How to avoid:**
Add a Windows-native detection branch (e.g. `tasklist /FI "IMAGENAME eq calibre.exe"` via
`subprocess`, parsed) alongside the pgrep/ps branches, gated on `sys.platform`. Keep the
fail-closed default for the genuinely-can't-detect case, but make "can't detect" actually rare
on Windows rather than the default outcome.

**Warning signs:**
Any manual Windows CLI write test that mysteriously refuses "Calibre is open" when Calibre is
provably closed — this is the fail-closed branch firing because neither tool exists, not an
actual open Calibre.

**Phase to address:**
Cross-platform core phase (Windows path handling). Verify with the Windows CI lane / manual
pass: run `scourgify apply --apply` on Windows with Calibre genuinely closed and confirm it
proceeds rather than refusing.

---

### Pitfall 8: Qt5→Qt6 enum usage breaks silently, not at import time [ECOSYSTEM]

**What goes wrong:**
Calibre 6+ auto-redirects `from PyQt5 import ...` to PyQt6 under the hood, so import statements
in `wizard.py`/`ui.py`/plugin dialog code don't need rewriting to *run* — but PyQt6 requires
fully-qualified enum access (e.g. `Qt.AlignmentFlag.AlignCenter` instead of the PyQt5-era
`Qt.AlignCenter`), and Calibre's compatibility shim only covers "the most common enums," not
all of them. Code using an enum outside that shimmed set imports fine and only fails at the
line that actually uses the enum — at runtime, potentially deep inside a rarely-exercised UI
branch (an error dialog's icon flag, a rare alignment constant), which is exactly the kind of
thing a developer testing the happy path on their own machine won't hit.

**Why it happens:**
The shim is a compatibility patch over a real breaking API change (Qt6 restructured most enums
into scoped nested classes), and "most common" is an incomplete set by definition.

**How to avoid:**
Any new Qt code (dashboard, settings dialog, marks-review panel — all net-new in this
milestone) should use fully-qualified PyQt6-style enum paths from the start rather than relying
on the shim, even though Calibre still nominally supports Qt5-era Calibre versions down to 6.0
(`minimum_calibre_version` per the Constraints). Grep new/changed plugin UI code for bare
`Qt.<EnumMember>` patterns before merging.

**Warning signs:**
An `AttributeError` on a Qt enum name that only surfaces when a specific dialog state is
reached (not at plugin load). Any copy-pasted PyQt5 example code from older Calibre plugin
tutorials.

**Phase to address:**
Both dashboard (#60) and marks-review (#61) phases, since they are the two phases writing
substantial new Qt widget code from scratch; add a lint/grep check for bare (non-scoped) enum
access as part of those phases' definition of done.

---

### Pitfall 9: Marks-based review can silently clobber the user's own manual marks [FIRST-PARTY — spec-anticipated]

**What goes wrong:**
Calibre's marked-books feature (`db.set_marked_ids`) is a single global set per library — there
is no namespace for "scourgify's marks" vs "the user's own marks" they set manually before
opening the review panel. If the review flow simply calls `set_marked_ids()` with the proposal
set, any marks the user had set themselves (e.g. to track their own reading progress or a
manual batch operation) are silently overwritten and lost when review starts, and never
restored.

**Why it happens:**
`set_marked_ids` replaces the whole marked set; there's no "add to" or "remember what was there
before" built into the Calibre API itself — that bookkeeping is entirely the plugin's
responsibility.

**How to avoid:**
This exact risk is already named in the NLSpec (B8.1): "the user's current context — existing
marked books, active virtual library/search — is snapshotted before review and restored after."
The snapshot/restore must capture the *exact prior marked-id set* (not just "were there any
marks") before calling `set_marked_ids()` for review, and restore it unconditionally on panel
close — including the abnormal-close path (Calibre crash, force-quit mid-review), which a
try/finally alone won't cover across a process restart. Consider persisting the pre-review
snapshot to a small state file that a startup check can detect and offer to restore ("scourgify
review was interrupted — restore your previous marks?").

**Warning signs:**
A review session tested only when the user had zero marks beforehand — the bug is invisible
under that condition. Test explicitly: mark 5 books manually, open scourgify review, close
scourgify's Calibre process uncleanly (kill -9 equivalent), reopen, check marks.

**Phase to address:**
Marks-based review phase (#61) — this is the phase's own stated precondition (B8.1) and
"prototype-first — this is the phase with no precedent anywhere" per the spec; the abnormal-
close case specifically needs its own test, not just the clean-close path the spec's edge
cases enumerate.

---

### Pitfall 10: Cost estimates and refusal rates are measured, not documented facts — and go stale silently [FIRST-PARTY]

**What goes wrong:**
Every number the plugin's engine picker and cost gate rely on — `engines.TRAITS['out_tokens']`
(Gemini's hidden-thinking-token overhead), the 14% Gemini `PROHIBITED_CONTENT` refusal rate,
the synopsis `CHUNK` size (chars/token ratio for the bundled Apple model), the adequacy-prompt
keep/generate ratio — are **measured against one library on one date**, not derived from any
vendor-published spec. `CONCERNS.md` explicitly flags all of these as "measured parameters at
risk": if Apple updates the bundled model, if Gemini changes its safety filter, or if the model
underlying `engine apple` changes context size, every one of these numbers silently goes wrong
— and because B4.3 makes the displayed cost estimate the *only* gate on irreversible cloud
spend (no confirmation dialog, by design — see Out of Scope), a stale estimate isn't a cosmetic
inaccuracy, it's a broken safety gate.

**Why it happens:**
There is no API contract from Apple/Google/OpenAI for "how many hidden tokens will this cost"
or "what fraction of mature content will this refuse" — these are empirical facts about a
specific model version, and model versions change without the API's request/response shape
changing, so nothing here trips a type error or a test failure when it drifts.

**How to avoid:**
Keep the measured values in source comments with their measurement date (already done). Add a
lightweight periodic re-measurement check (even manual — "re-measure before each release
against the current bundled model") rather than treating the numbers as permanent. For the
plugin specifically: surface the measurement date next to the estimate in the UI ("estimate as
of 2026-07-30") so a stale number reads as stale rather than authoritative — a UI plugin
audience is far less likely to read `CONCERNS.md` than a CLI power user reading source.

**Warning signs:**
A classify run's actual bill diverging significantly (>20%) from the pre-run estimate. Apple OS
updates around "Apple Intelligence" model versions (macOS point releases are the trigger to
watch). Gemini API changelog entries about safety filtering.

**Phase to address:**
The engine picker work already in progress (existing, pre-milestone) plus this milestone's
dashboard phase, which is a second UI surface rendering the same numbers — both should carry
the measurement date, not just the number, per the recommendation above.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|-----------------|------------------|
| Returning `[]`/`{}` for "file not found" in resource loaders (`_read_vocab_file`, historically `load_maps`) | Simpler call sites, no try/except everywhere | Silent wrong answers inside a zip context where "not found" is the *normal* case, not an error (Pitfall 2) | Never — always distinguish "legitimately empty" from "could not read" |
| Bare `except: pass` around `chmod` (Pitfall 6) | Doesn't crash settings save on unusual filesystems | Silent security regression (world-readable key) with zero signal | Only with a logged warning attached; never silent |
| Reusing the CLI's `pgrep`/`ps` open-detection unmodified for the plugin's Windows CLI surface | Zero new code for phases 1–5 | Windows CLI writes fail-closed by default (Pitfall 7) | Acceptable until the Windows support phase — must be fixed before that phase's exit criteria |
| Skipping a switch-during-open-menu regression test because "it worked in manual testing" | Faster phase-4 ship | The exact bug class (Pitfall 1, Pitfall 5) that has already recurred once in this codebase | Never for library-identity-sensitive code; acceptable for pure-display code with no write consequence |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|-----------------|-------------------|
| Calibre `new_api` / legacy `db` split | Assuming `new_api` covers everything (marked books, some legacy-only calls) and holding only one handle | Keep both handles where needed (`db.set_marked_ids` has no `new_api` equivalent per Pitfall/ECOSYSTEM finding); document which calls need which handle per behavior |
| `ThreadedJob` / `Dispatcher` | Touching any Qt widget from inside the job worker function, even "just to read a value" | Every completion callback that touches the GUI is `Dispatcher`-wrapped (already enforced by `tests/test_plugin_source.py`); a worker function may only return data, never call Qt methods directly |
| Gemini API | Treating `PROHIBITED_CONTENT` refusals as rare/edge-case (docs imply ~1%) | Budget for ~14% on fanfiction content (measured on this library); route refusals to a second engine automatically via the "Retry on <engine>" verb (B1), never assume a refusal is a one-off |
| LLM engine keys via env vs. stored config | Assuming "stored config wins if present" (the more intuitive default) | scourgify deliberately reverses this for keys — env wins over stored — the opposite rule from the library-path injection seam; must be documented at every settings surface (dashboard, CLI help) or it reads as a bug |
| MobileRead plugin index | Publishing without an active MobileRead forum thread for support/updates, or without following the existing per-plugin-thread convention other plugins use | Every listed plugin has its own MobileRead thread that doubles as the changelog/support channel; the milestone's "drafted post, user posts it" approach matches this norm — don't skip having *a* thread even if posting is deferred |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|-----------------|
| Assuming any core read is "cheap enough for the GUI thread" | Visible stutter on click | `wrangle.load_maps()` measured at **870 ms** against the real 7,949-book library — already exceeds a frame; treat as a job-dispatch requirement for *any* new read, not just writes | Immediately, at real-library scale; invisible on a small throwaway library used for manual testing |
| Apple engine assumed "fast enough" because it's free | Interactive classify/synopsis actions hang for minutes | Apple is single-threaded, ~40 s/book (synopsis) — fine for a background sweep, wrong default for an interactive dashboard action on more than a few books; the dashboard must size batches or force a cloud engine for interactive use | Any selection over roughly a dozen books |
| Dashboard header recompute triggered too eagerly | GUI feels laggy after every action even though invalidation is well-designed | The spec already scopes recompute to "after every completed scourgify job and on dashboard open" (B6.1) — do not add ad hoc recomputes (e.g. on every selection change) without re-measuring | If a future feature adds a recompute on a high-frequency event (selection change, hover) |
| `ebook-convert` timeout (180 s, hard-coded) for non-EPUB synopsis extraction | One large PDF/MOBI can halt a whole background sweep batch | Documented in `CONCERNS.md`; not yet fixed — worth flagging for whichever phase makes the sweep interactive/dashboard-visible, since a stuck batch is far more visible in a dashboard than in a background CLI sweep | Any sufficiently large non-EPUB file in the library |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| API key echoed into an HTTP error message that lands in a job log, CSV reason column, or error dialog | Key exfiltration via any of three surfaces (already named as a risk in the spec, B5 postcondition) | `engines.redact()` is the single point where every reason string is built — never add a second place that formats an exception message; any new failure-reporting surface (dashboard toast, marks-review error panel) must route through the existing `reason` string, not re-stringify the raw exception |
| Treating `chmod 0600` as sufficient key protection cross-platform | False sense of security on Windows/managed filesystems (Pitfall 6/7) | State the actual protection level per-platform in the settings UI rather than implying uniform protection |
| Assuming Gemini/OpenAI refusal behavior implies content-safety coverage the plugin can rely on | A book refused by one engine is not "safe," and a book *accepted* by another engine is not necessarily appropriately handled — refusal rate differs by engine and isn't documented per-engine anywhere but this codebase's own measurements | Keep refusal-rate facts as `TRAITS` flags (already the pattern) rather than embedding assumptions in UI copy; the dashboard/settings surfaces must derive claims from `TRAITS`, never hardcode a rate |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|------------------|
| "No confirmation dialogs" (deliberate design choice) applied inconsistently to a genuinely destructive new verb | A user clicks something they didn't mean to, with no undo net for that specific action | The spec's actual contract is "the price/consequence is on the control, and undo (B7) is the net for writes" — every new verb added in this milestone must have *either* a price-on-control *or* a full undo path before it ships without a confirmation; a verb with neither is the actual pitfall, not "no dialogs" itself |
| Grey-out-with-no-reason for an unavailable menu verb | User can't tell if it's a bug, a missing key, or genuinely not applicable | The existing convention (fixed-slot, greyed with a stated reason, never hidden) must be followed for every new verb — enforced already by the "slot never changes meaning" rule; don't let a new dashboard panel invent a different "hide when unavailable" pattern |
| Restart-to-apply column creation happening without warning mid-workflow | User loses in-progress context (open dialogs, selection) unexpectedly | B6.5 already scopes this to first-run/unhealthy-library setup mode — must not be triggered from deep inside an unrelated workflow; keep column creation strictly inside setup mode, never as a side-effect of another action discovering a missing column |
| Marks-review panel silently discarding undecided proposal rows on close | User believes they reviewed everything; some rows quietly vanish | B8.4 already specifies "pending = proposal rows minus decided rows, recomputable at any time" — the review panel's close action must never re-archive/delete undecided rows, only mark decided ones; this needs its own explicit test since it's easy to accidentally archive the whole batch on any close |

## "Looks Done But Isn't" Checklist

- [ ] **Custom column creation:** Often missing the desync check — verify a second, unrelated
      Calibre panel (Tag Browser) does NOT reflect the new column pre-restart (Pitfall 4);
      "looks done" if only the scourgify dashboard is checked.
- [ ] **Multi-library dashboard/review:** Often missing per-library namespacing — verify with
      two throwaway libraries open in sequence, not one (Pitfall 3); a single-library demo
      always looks done.
- [ ] **Marks-based review:** Often missing pre-existing-marks preservation on the *abnormal*
      close path (crash, force-quit) — verify the clean-close path is not the only one tested
      (Pitfall 9).
- [ ] **Windows CLI parity:** Often missing the process-detection fallback — verify
      `scourgify apply --apply` actually proceeds on Windows with Calibre closed, not just that
      it doesn't crash (Pitfall 7); "no error" and "correct behavior" are different outcomes
      here since the fail-closed branch also produces "no error," just the wrong refusal.
- [ ] **Any new Qt dialog:** Often missing fully-qualified PyQt6 enum usage in the
      rarely-exercised branches (error icons, rare alignment/flag combinations) — verify by
      deliberately exercising error/edge states, not just the primary happy-path layout
      (Pitfall 8).
- [ ] **Engine picker / cost display in any new UI surface:** Often missing the measurement
      date next to the number — verify the dashboard's cost display doesn't silently duplicate
      a stale assumption from a different code path (Pitfall 10).
- [ ] **API key handling in any new error surface:** Often missing redaction — verify by
      deliberately triggering an auth failure and inspecting every new UI surface (toast,
      dashboard error state, marks-review error) for the raw key, not just the existing CLI/log
      paths that are already covered.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|-----------------|
| Guard that can't fire (Pitfall 1) | LOW | Once identified, the fix is a one-line change (read from the live source, not a captured path) plus one adversarial test; the danger is entirely in *not noticing*, not in fixing |
| Empty resource maps from inside the zip (Pitfall 2) | MEDIUM | If shipped, symptoms are silent wrong-normalization or under-quoted classify costs — no crash to alert anyone; recovery requires a release, plus auditing whether any real user got an under-quoted bill they'd dispute |
| Multi-library artifact bleed (Pitfall 3) | MEDIUM | No data corruption (read-only bleed observed so far), but any write feature built on top of misattributed artifact state before this is fixed needs re-audit for whether a write happened against the wrong book's history |
| Marks clobbered (Pitfall 9) | HIGH if the user's own marks encoded meaning not recorded elsewhere (Calibre marks are not itself an undo-tracked concept) | Cannot be recovered programmatically after the fact — this is why the snapshot/restore must be verified working *before* the feature ships, not patched after a user loses marks |
| Windows fail-closed write refusal (Pitfall 7) | LOW | User-visible immediately (every write refuses); once diagnosed, add the `tasklist`-based branch; no data at risk since it fails toward safety |
| Silent chmod failure (Pitfall 6) | LOW–MEDIUM | Add logging + one-time warning; for a user who already saved a key under the silent-failure condition, no way to retroactively know it happened without the logging fix landing first |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|-------------------|---------------|
| Guard reads back its own write (P1) | Every phase adding a new identity/freshness check (esp. #60 dashboard, #61 review, write-lock) | Adversarial test: trigger the failure mid-flight, not just the happy path |
| Zip-bundled resources silently empty (P2) | Bundled-`defaults`-readable-from-zip seam (Active item; blocks #59 and any cost-displaying UI) | Smoke test importing core modules from a *built* plugin zip, asserting non-empty `load_maps()`/`load_vocab()` |
| Multi-library artifact bleed (P3) | Same seam-landing phase as P2 (folded into phase 6 per NLSpec 2026-08-11 decision); blocks #60, #61 | Repro with two throwaway libraries; assert no cross-library artifact rendering |
| `create_column` GUI desync (P4) | In-plugin first-run setup phase | Verify an unrelated Calibre panel does not reflect the new column pre-restart |
| Dynamic menu + library switch crash (P5) | Multi-library work folded into phase 6; dashboard phase #60 inherits it | Regression test: open menu, switch library mid-open, assert no crash |
| Silent `chmod` failure (P6) | Windows support phase (cross-platform core); trivial fix independent of platform | Log + surfaced warning; test on a filesystem where chmod raises |
| `pgrep`/`ps` absence on Windows (P7) | Cross-platform core phase | Windows CI lane / manual pass: CLI write proceeds with Calibre genuinely closed |
| Qt5→Qt6 enum breakage (P8) | Dashboard (#60) and marks-review (#61) — the two phases writing new Qt widget code | Grep/lint for bare (unscoped) enum access as part of phase exit criteria |
| Marks clobbered on review (P9) | Marks-based review phase (#61) — spec's own B8.1 precondition | Manual-marks-preserved test across both clean close AND abnormal close |
| Stale measured cost/refusal numbers (P10) | Existing engine-picker work + dashboard phase (#60), as a second surface rendering the same numbers | UI shows measurement date; process note to re-measure before each release |

## Sources

- **First-party (this repo, HIGH confidence — directly measured/observed in this codebase):**
  `docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md` v1.1.0 (phase-1 through phase-5
  amendments, each dated and tied to an issue number: #54, #55, #56, #57, #58); the atomicity
  contract; B1–B8 edge-case sections.
  `.planning/codebase/CONCERNS.md` (2026-08-28 audit) — Performance Bottlenecks, Measured
  Parameters at Risk, Fragile Areas, Plugin Architecture Constraints, Known Issues (Already
  Fixed), Security Considerations, Test Coverage Gaps sections.
  `CLAUDE.md` (project instructions) — engine facts (Gemini 14% refusal, hidden-thinking
  tokens), cost-testing gotchas, `calibre_open()` behavior.
- **Ecosystem (MEDIUM confidence, cross-checked web sources, 2026-08-28 web research):**
  - [Plugin devs: Upcoming migration to Qt 6 — MobileRead Forums](http://www.mobileread.mobi/forums/showthread.php?t=344064) (Qt5→Qt6 enum/icon breaking changes)
  - [API documentation for plugins — calibre 9.13.0 documentation](https://manual.calibre-ebook.com/plugins.html)
  - [The Graphical User Interface — calibre 9.13.0 documentation](https://manual.calibre-ebook.com/gui.html)
  - [Customizing calibre — calibre 9.13.0 documentation](https://manual.calibre-ebook.com/customize.html) (`CALIBRE_CONFIG_DIRECTORY`, `CALIBRE_DEVELOP_FROM`, `CALIBRE_TEMP_DIR`)
  - [DeDRM_tools CALIBRE_CLI_INSTRUCTIONS.md](https://github.com/noDRM/DeDRM_tools/blob/master/CALIBRE_CLI_INSTRUCTIONS.md) (JSONConfig plaintext key storage precedent)
  - [PEP 594 has been implemented: Python 3.13 removes 20 stdlib modules — Python.org Discussions](https://discuss.python.org/t/pep-594-has-been-implemented-python-3-13-removes-20-stdlib-modules/27124) (confirms `imp`/`distutils` removed in 3.12, `cgi`/`cgitb` removed in 3.13 — both gone well before Calibre's bundled 3.14.6)
  - MobileRead Forums — "Plugins" subforum norms (per-plugin support thread convention):
    [MobileRead Forums Plugins](https://www.mobileread.com/forums/forumdisplay.php?f=237)
  - General Qt/PyQt thread-safety guidance (corroborating, not Calibre-specific):
    [Use PyQt's QThread to Prevent Freezing GUIs — Real Python](https://realpython.com/python-pyqt-qthread/)

---
*Pitfalls research for: Calibre GUI plugin (write actions, dashboard, marks-review, in-plugin
setup, Windows support, public distribution)*
*Researched: 2026-08-28*
