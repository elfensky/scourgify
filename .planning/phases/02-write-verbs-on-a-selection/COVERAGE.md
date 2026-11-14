# API Coverage — Phase 2 (Write verbs on a selection)

No external API integration: this phase wires a Qt surface and a job layer onto scourgify's own
already-shipped `engines.py` adapters and `common.write_ops` write funnel — it adds no provider, no
endpoint, no SDK and no new capability surface, and installs no package (see 02-RESEARCH.md
`## Package Legitimacy Audit`, which records the same finding).

The deterministic detector run at plan time over this phase's CONTEXT and ROADMAP section returned
`{"detected": false}`. This declaration is recorded so the seal-time re-run over the PLAN bodies —
which necessarily mention engines, API keys and HTTP because the phase renders their existing
results — resolves to the same, examined answer rather than to an unreviewed signal.

**What the plugin exposes of the existing engine surface, for the record:** every engine
`engines.usable_engines()` reports on the host, rendered from `engines.engine_rows` /
`engines.engine_options` / `engines.TRAITS`. Nothing is filtered out by the plugin, so the GUI's
engine roster is the CLI's engine roster by construction — an engine added to `ENGINES` appears in
both front doors by existing.
