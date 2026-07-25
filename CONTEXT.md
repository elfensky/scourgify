# Domain glossary

The ubiquitous language of scourgify. Use these terms in code, docs, and reviews — one name per
concept.

- **Wrangle** — the deterministic normalization engine (`wrangle.py`): alias→canonical folds,
  junk-drop, routing between columns. Never calls an LLM.
- **Plan** (wrangle) — ONE full-library transform pass (`wrangle.plan(cfg, maps)`), computed once;
  `preview()` / `guard()` / `step()` / `write()` all read it. The CLI and the wizard drive the same
  object — never a recompute between preview and write.
- **Decisions** — transform's own per-value log (`kind, where, before, after`), emitted via
  `transform(..., log=)`. The audit's "examples of what would change" read this log; no report
  re-derives (and desyncs from) the rules.
- **Run plan** (classify) — `classify.plan(opts)`: scope + resume resolved ONCE into
  `{targets, todo, …}`; the wizard prices/confirms over `todo` and `classify_run` executes the same
  plan, so the confirmed cost is the billed cost.
- **Report** — `report.py`, the ONE owner of the rich-or-plain rendering policy for the core tools
  (`table`/`tree`/`say` + the live `Dashboard`). `ui.py` stays rich-required (wizard only);
  `_writer.py` imports neither.
- **Booktext** — `booktext.py`, the text extractor behind `--text-fallback`: `paths(con)` picks each
  book's best format (EPUB preferred), `extract(path)` samples prose (EPUB-as-zip, else
  `ebook-convert`).
- **Classify** — LLM content tagging from the **controlled vocabulary**; produces the **proposal**.
- **Engine** — one LLM adapter (apple/claude/openai/gemini/mistral) behind the seam in
  `engines.py`. `_post_json` is the transport; a fake engine or a monkeypatched transport stands in
  for the network in tests. What an engine is *like* is data there too (`TRAITS` + `is_free` off
  `PRICING` + `max_workers`) — callers never string-test the name.
- **Artifact** — a CSV the tools hand each other under `data/`, owned by `artifacts.py`:
  - **Proposal** (`classify_proposal.csv`) — per-book `added_tags` (vocab, applied) +
    `proposed_new` (novel candidates, never applied directly).
  - **Ranked candidates** (`classify_newtags_ranked.csv`) — aggregated `proposed_new` with
    nearest-existing verdicts, feeding promote.
  - **Review** (`promote_review.csv`) — promote's adjudicated verdicts awaiting human apply.
  - **Ledger** (`promote_ledger.csv`) — every decided candidate; feeds backfill and skip-on-rerun.
  - **Failures** (`classify_failures.csv`) — books an engine errored on (retry with another
    engine); written and read via `artifacts.py` like every other artifact.
  - **Archiving** — a consumed artifact is renamed `*_applied_*` / `*_discarded_*` so stale rows
    can never re-apply.
- **Override files** — the user's overrides dir (config `[overrides] dir`, resolved ONCE by
  `overrides.overrides_dir`) and its formats (headers, `,`-vs-`;` delimiter sniffing,
  append-if-absent, the vocab `-term` removal) are owned by `overrides.py`
  (`ov_path` / `append_lines` / `append_rows` / `merge_vocab` / `read_aliases`); promote's folds
  and the rejects→overrides flow write through it, and wrangle/classify/setup read through it —
  a relocated dir can never split writers from readers.
- **Promote** — adversarial adjudication (advocate → skeptic → human referee) of ranked
  candidates into promote / alias / reject.
- **Backfill** — deterministically applying promoted/aliased tags onto the books that first
  proposed them (no LLM). One implementation, in `promote.py`; the wizard delegates to it.
- **Ops** — the write instructions sent to `_writer.py` under calibre-debug. Built only by the
  `op_*` constructors in `common.py`, next to the `run_writer` wipe guard that inspects them.
- **Spend gate** — the single cloud-cost confirmation (`classify.spend_gate`). The wizard's
  cost-estimate confirm *answers* it (`yes=True`); it is never re-implemented.
- **Interactive** — `common.interactive()`: the one answer to "is a human at the terminal"
  (stdin+stdout TTYs, no CI/NONINTERACTIVE). `common.confirm` is the plain y/n; `ui.confirm` its
  rich twin for wizard surfaces.
- **Stamp** (`#wrangled`) — the per-book datetime marker meaning "classify processed this book";
  state lives in the library, so selection (`select.py`) needs no external file.
