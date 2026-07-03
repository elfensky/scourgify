# attic/ — the original single-purpose pipeline, kept as provenance

`wrangle.py` (+ `classify.py` / `staleness.py`) supersedes these. They are not maintained,
but two remain occasionally useful standalone:

- `apply_fff_config.py` — FanFicFare config fix (now also offered interactively by `wrangle.py setup`)
- `apply_relationships.py` — one-off #relationships rebuild from `relationships_rebuild.csv`

These scripts read their CSV maps from **their own directory**; the personal maps now live in
`../data/` — copy or symlink what you need before running one. They also predate the
standalone/write split and expect `calibre-debug -e <script>` with Calibre closed, and they do
NOT make the automatic metadata.db backup the current tools do — back up first.
