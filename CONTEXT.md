# Domain glossary

The ubiquitous language of scourgify. Use these terms in code, docs, and reviews — one name per
concept.

- **Wrangle** — the deterministic normalization engine (`wrangle.py`): alias→canonical folds,
  junk-drop, routing between columns. Never calls an LLM.
- **Classify** — LLM content tagging from the **controlled vocabulary**; produces the **proposal**.
- **Engine** — one LLM adapter (apple/claude/openai/gemini/mistral) behind the seam in
  `engines.py`. `_post_json` is the transport; a fake engine or a monkeypatched transport stands in
  for the network in tests.
- **Artifact** — a CSV the tools hand each other under `data/`, owned by `artifacts.py`:
  - **Proposal** (`classify_proposal.csv`) — per-book `added_tags` (vocab, applied) +
    `proposed_new` (novel candidates, never applied directly).
  - **Ranked candidates** (`classify_newtags_ranked.csv`) — aggregated `proposed_new` with
    nearest-existing verdicts, feeding promote.
  - **Review** (`promote_review.csv`) — promote's adjudicated verdicts awaiting human apply.
  - **Ledger** (`promote_ledger.csv`) — every decided candidate; feeds backfill and skip-on-rerun.
  - **Archiving** — a consumed artifact is renamed `*_applied_*` / `*_discarded_*` so stale rows
    can never re-apply.
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
