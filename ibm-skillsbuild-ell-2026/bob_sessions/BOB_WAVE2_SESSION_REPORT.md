# IBM Bob Engineering & Co-Pilot Session Report (Wave 2 — Nov 11, 2026)

## 1. Configured IBM Bob MCP Tooling (`bob-mcp-config.json`)
- **Active Local MCP Servers (6):** `filesystem`, `memory`, `pdf`, `sequential-thinking`, `puppeteer`, `chrome-devtools`
- **Unconfigured Placeholders (2):** `notion`, `browserstack` (kept with placeholder env values; not active in test or runtime paths)

## 2. Documented Wave 2 Hardening Trace (IBM Bob Task ID `7f96499e-4d37-4fd1-bd16-52cb3628400a`)
Full file-edit timestamps are preserved in [`wave2-bob-edit-trace.jsonl`](./wave2-bob-edit-trace.jsonl) (`67` recorded operations on `2026-11-11`):
- **Mainframe & EBCDIC Integrity:** Hardened [`mainframe_adapter.py`](../mainframe_adapter.py) (fixed-width 80-column `cp037` round-trip validation, strict length checks, honest offline simulation reporting when no live IBM MQ QM1 queue manager is attached).
- **Bounded Caseworker Co-Pilot & Tech Stack Showcase:** Mounted `/copilot` and `/api/bob/*` directly on [`app.py`](../app.py); updated [`bob_conversational_demo.py`](../bob_conversational_demo.py) and [`bob_chat.html`](../bob_chat.html) so deterministic `hard_lock` / `INPUT_INVALID` states veto write-back, all offline simulations report `simulated: true`, and Judges/Reviewers can query the live *"Tech Stack & IBM Bob"* view.
- **Fail-Closed Statutory Adapters:** Updated [`compliance_engine.py`](../compliance_engine.py) and `agencies/` (`fema_adapter.py`, `va_adapter.py`, `dua_adapter.py`) to reject explicit `None` and malformed types as `INPUT_INVALID` instead of falling through.
- **Validation Portal & Surge Limits:** Hardened [`surge_api.py`](../surge_api.py) and [`validation_routes.py`](../validation_routes.py) for `RULE_PACK_VERSION = "2026-11-11-wave2"` across `30` whitelisted CFR sections and `7` programs.
- **Container & eCFR Parity:** Added `HEALTHCHECK` and `--preload` in [`Dockerfile`](../Dockerfile), verified `30/30` parity via `ecfr_check.py --check-parity`, and passed `424` total tests (`383` in `asce-demo` @ `92%` coverage on `app.py` + `41` root tests).
