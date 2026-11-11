# ASCE GOVBEN CaaS v10.0.0 — IBM SkillsBuild AI Experiential Learning Lab (2H 2026)

**Team:** ASCE (`ASCE Technologies LLC`, SAM.gov CAGE `26NM9`, UEI `T5SKKW6K5W55`, D-U-N-S `14-873-9839`, IBM Partner Plus CEID `10x6kg`)  
**Track:** Track #4 — Government & Public Services (Personal & Team Productivity)  
**Lead Architect & Inventor:** Todd Bullitt (`t.bullitt@ascetech.org`)  
**IP Notice:** USPTO Provisional Patent Application `#64/168,240` (Patent Pending; not yet a granted patent)  
**Data & Deployment Boundary:** **100% synthetic demonstration prototype.** Not FedRAMP-authorized, not integrated with live agency production systems, and never processes real citizen PII.

---

## 1. Live Endpoints & Master Showcase Video (`2m 27.4s` • `1080p H.264` • `7,281,141 B`)

- **Live Gateway (IBM Cloud Code Engine):** `https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/`
- **Interactive Co-Pilot & Tech Stack (`/copilot`):** `https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/copilot`
- **Synthetic Validation Portal (`/validate`):** `https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/validate` and `https://ascetech.org/validate`
- **Live Cloud Video Stream:** `https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/static/ASCE_3Min_Demo_Video.mp4`
- **GitHub Direct Video Stream:** [`ASCE_3Min_Demo_Video.mp4`](./ASCE_3Min_Demo_Video.mp4) (`7,281,141` bytes)

---

## 2. Architecture & Dual IBM Bob Role (`bob-mcp-config.json` + `/copilot`)

ASCE operates as a **deterministic pre-commit compliance gate** anchored by fail-closed Python statutory rules (`DeterministicAuditor` in [`compliance_engine.py`](./compliance_engine.py)) that always take precedence over probabilistic generative explanations:

1. **Deterministic Core & Fail-Closed Veto (`RULE_PACK_VERSION = "2026-11-11-wave2"`):**
   - Evaluates **`30` whitelisted CFR sections across `7` federal/state benefit programs** (`FEMA_IA` 44 CFR 206, `VA` 38 CFR 3, `SSA` 20 CFR 404/416, `MEDICAID` 42 CFR 435, `SNAP` 7 CFR 273, `HUD` 24 CFR 5/960/982, `DUA` 20 CFR 625), verified at **`30/30` parity** (`ecfr_check.py --check-parity`).
   - Enforces fail-closed `INPUT_INVALID` and `HARD LOCK` gates: deterministic violations veto `APPROVE_FOR_REVIEW` unless an explicit human supervisor override is recorded.
   - Generates fixed-width **80-column ASCII + EBCDIC (`cp037`) COBOL copybook records** ([`mainframe_adapter.py`](./mainframe_adapter.py)) and OpenCV wet-ink signature inspection ([`vision_ai.py`](./vision_ai.py)).
2. **IBM `watsonx.ai` Advisory & Whitelist Gatekeeping:**
   - Default model [`ibm_watsonx_client.py`](./ibm_watsonx_client.py): `ibm/granite-4-h-small` (configurable via `IBM_WATSONX_MODEL`, including `meta-llama/llama-3-3-70b-instruct` evaluated in Wave 2 live benchmarks).
   - [`validate_citation`](./app.py) checks every model-cited CFR reference against the program whitelist (flagging unverified or cross-title citations as `UNVERIFIED` — whitelist matching only, not legal verification).
3. **IBM Bob Dual Usage:**
   - **SDLC & IV&V Engineering Co-Developer ([`bob-mcp-config.json`](./bob-mcp-config.json)):** Built and hardened across local MCP servers (`filesystem`, `memory`, `pdf`, `sequential-thinking`, `puppeteer`, `chrome-devtools`; `notion` and `browserstack` remain unconfigured placeholders). Session edit trace exported in [`bob_sessions/`](./bob_sessions/BOB_WAVE2_SESSION_REPORT.md).
   - **Bounded Runtime Caseworker Co-Pilot (`/copilot`, [`bob_conversational_demo.py`](./bob_conversational_demo.py) & [`bob_chat.html`](./bob_chat.html)):** Follows *"Bob explains, DeterministicAuditor decides"*. Explains statutory blocks in plain English and blocks write-back whenever `hard_lock` or `INPUT_INVALID` is active. Standalone demo responses explicitly expose `simulated: true` (`live_ibm_bob_connected: false`, `live_watsonx_orchestrate: false`, `live_mainframe_connected: false`).

---

## 3. Verified Wave 2 Test & Benchmark Evidence (Nov 11, 2026)

| Verification Check | Measured Result |
| --- | --- |
| Automated Pytest Suite (`asce-demo`) | **`383` passed**, `0` failed (`92%` coverage on `app.py`) |
| Root Utility Tests (`ecfr_check`, `metrics_collector`) | **`41` passed**, `0` failed (**`424` total pytest passing**) |
| Statutory Whitelist Parity (`ecfr_check.py --check-parity`) | **`30/30` (`ok: true`)** across 7 programs |
| Repo Cross-Agency CFR JS Suite (`run-cfr-tests.js`) | **`8/8` passed** |
| `10,000`-Case Surge Stress Test (`/api/v1/batch`) | **`21.22s`** (`471.3 cases/sec`), `4,979/4,979` deadlines, `4,934/4,934` redundancies, `100/100` malformed isolated, **`0` mismatches** |
| Live `watsonx.ai` Inference Benchmark (`Part B`) | **`42/42` calls** (`meta-llama/llama-3-3-70b-instruct`), `42/42` valid JSON, `36` `VALIDATED` / `6` `UNVERIFIED`, `20` invented evidence items rejected |
| `5,000`-Packet Portal Benchmarks (`/api/validate/benchmark`) | `HUD_24_CFR`: **`635 ms`** \| `FEMA_44_CFR`: **`3,218 ms`** \| `MEDICAID_42_CFR`: **`3,410 ms`** (`0` schema failures) |
| Red Hat UBI 9 Container Certification | Non-root `USER 1001`, OpenShift Preflight **`11/11 Pass`** (`v10.0.0`), Security Health Index **`Grade A`** |

---

## 4. Core Deliverables in This Folder

- [`ASCE_3Min_Demo_Video.mp4`](./ASCE_3Min_Demo_Video.mp4) & [`demo.html`](./demo.html) — `2m 27.4s` `1080p` `H.264` walkthrough & HTML5 player
- [`app.py`](./app.py), [`compliance_engine.py`](./compliance_engine.py), [`mainframe_adapter.py`](./mainframe_adapter.py), [`validation_routes.py`](./validation_routes.py), [`surge_api.py`](./surge_api.py) — Wave 2 Flask gateway, statutory rule engine, EBCDIC copybook generator, & batch/portal APIs (`asce_core.py` retained as historical v2 prototype reference)
- [`bob_conversational_demo.py`](./bob_conversational_demo.py), [`bob_chat.html`](./bob_chat.html), [`bob-mcp-config.json`](./bob-mcp-config.json), [`bob_sessions/`](./bob_sessions/BOB_WAVE2_SESSION_REPORT.md) — Bounded Co-Pilot & IBM Bob MCP/session records
- [`ASCE_IBM_SkillsBuild_Showcase_Presentation.pptx`](./ASCE_IBM_SkillsBuild_Showcase_Presentation.pptx) — Showcase Pitch Deck (Wave 2 updated)
- [`Solution_Design_Guide_ASCE_Final.pptx`](./Solution_Design_Guide_ASCE_Final.pptx) — Solution Design Guide (Wave 2 updated)
- [`ASCE_IBM_Submission_Package_Prime.docx`](./ASCE_IBM_Submission_Package_Prime.docx) & [`ASCE_TRL6_Engineering_Build_Progress_Report.docx`](./ASCE_TRL6_Engineering_Build_Progress_Report.docx) — Written submission & engineering validation reports (Wave 2 updated)

---

## 5. Verified Ecosystem Registrations

- **IBM Cloud Private Catalog:** `ASCE GOVBEN CaaS v10.0.0` — Offering ID `ac46087fe4924a7a98026981d3f0230c:o:17f34ac9-f6f2-4279-9ef3-8d91bd990d0c`, Product ID `85beaa09-c840-417e-82de-a61430d1e729`, ECCN `EAR99`, UNSPSC `43232606`
- **Red Hat Partner Connect:** Org ID `20792660`, Product ID `6ae3354d17b7f519e40a0c89`, Container Project PID `6ae487b483fefa429257d504` (`registry.access.redhat.com/ubi9/python-311`)
- **Public-Sector Distribution:** TD SYNNEX Public Sector Remarketer Customer `#0004605372`; Georgia Supplier (`GA@WORK`)
