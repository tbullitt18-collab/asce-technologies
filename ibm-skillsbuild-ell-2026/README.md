# ASCE GOVBEN CaaS v10.0 — IBM SkillsBuild AI Experiential Learning Lab (2H 2026)
**Team:** ASCE (`ASCE Technologies LLC`, IBM Partner Plus CEID `10x6kg`)  
**Track:** Track #4 — Government & Public Services (Personal & Team Productivity)  
**Lead Architect & Inventor:** Todd Bullitt (`t.bullitt@ascetech.org`)

---

## 1. Master Showcase Video (`2m 38.6s` • `1080p H.264`)
- **GitHub Direct Video Stream:** [`ASCE_3Min_Demo_Video.mp4`](./ASCE_3Min_Demo_Video.mp4)
- **Live Cloud Stream (IBM Cloud Code Engine):** `https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/static/ASCE_3Min_Demo_Video.mp4`
- **Live Cloud Interactive Co-Pilot Sidecar:** `https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/copilot`
- **Primary Enterprise Gateway:** `https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/`

### Video Highlights (6 Live Scenes):
1. **Zero-Touch Legacy Attachment & 4-Step `watsonx Orchestrate` Sync:** Attaches non-invasively alongside a Windows 2000 FEMA NEMIS-IA workstation via `EHLLAPI` (`4.2 ms` screen-buffer read) and verifies 3 wet-ink cursive strokes on a 200-DPI residential lease under `44 CFR § 206.115(e)`.
2. **Pre-Commit Interception (`CICS-HOLD-101`) & Conversational Triage:** Blocks wrongful `01G` denials and duplicate document request loops (`REDUNDANT_REQUEST_BLOCKED`) prior to `DB2 COMMIT`, with **IBM Bob** translating deterministic statutory findings into plain-English caseworker guidance.
3. **One-Click `EHLLAPI` Write-Back & 85% Caseworker Productivity Boost:** Eliminates manual hand-offs across 4 fragmented systems—cutting average review time from **42m 00s down to 00m 28s (-85%)** while injecting verified CFR rationale and a `SHA-256` Cloudant receipt directly into the legacy client.
4. **IBM z/OS CICS 3270 COBOL Copybook & 1.79s Surge Benchmark:** Formats fixed-width **80-column CCSID-500 EBCDIC** records (`mainframe_adapter.py`) onto IBM MQ and validates **5,000 synthetic claims in 1.79 seconds (`2,793 cases/sec`)** on Red Hat UBI 9 and IBM Cloud Code Engine.
5. **Multi-Agency State & Federal Switching:** Live switching across **Georgia DCH Medicaid GAMMIS (`42 CFR § 435.916` ex-parte wage match)**, **Georgia DFCS SNAP COMPASS (`7 CFR § 273.12` SAR-7 churn prevention)**, and **VA VBMS (`38 CFR § 3.159`)**.
6. **3-Layer Non-Invasive Legacy Hook Blueprint:** Production deployment via `EHLLAPI / Win32 UIA` (Read), `IBM MQ / CICS XPCFTCH` (Hold), and `80-Col EBCDIC + Cloudant SHA-256` (Write)—requiring **0 lines of legacy COBOL source changes**.

---

## 2. Personal & Team Productivity Impact (Track #4)
- **85% Reduction in Caseworker Review Time:** Cuts manual cross-system verification (switching between 200-DPI TIFF viewers, eCFR PDFs, state wage tapes, and 3270 mainframe screens) from **42 minutes to 28 seconds per case**.
- **Eliminates Fragmented Data & Manual Hand-Offs:** Automates the 4-step hand-off from document intake → deterministic statutory check → supervisor escalation/remediation → 80-column COBOL mainframe staging via **IBM Bob** and **IBM watsonx Orchestrate**.
- **Clears 8-Week Adjudication Backlogs & Protects SLA Scorecards:** Shields Federal Prime Contractor Program Directors and State Agency Directors from up to 15% SLA award-fee clawbacks and `$1,200–$4,500` ALJ remand costs while preventing wrongful citizen disenrollment.

---

## 3. Core Repository Deliverables in This Folder
- [`ASCE_3Min_Demo_Video.mp4`](./ASCE_3Min_Demo_Video.mp4) — 2m 38.6s 1080p Showcase Demonstration
- [`bob_conversational_demo.py`](./bob_conversational_demo.py) & [`bob_chat.html`](./bob_chat.html) — Docked IBM Bob Caseworker Co-Pilot & 4-Step watsonx Orchestrate Legacy Workstation Bridge
- [`compliance_engine.py`](./compliance_engine.py) — 100% Deterministic Multi-Agency Statutory Engine (`44 CFR`, `42 CFR`, `7 CFR`, `38 CFR`, `20 CFR`)
- [`mainframe_adapter.py`](./mainframe_adapter.py) — Fixed-Width 80-Column EBCDIC COBOL Copybook & IBM MQ Last-Mile Adapter
- [`ASCE_IBM_SkillsBuild_Showcase_Presentation.pptx`](./ASCE_IBM_SkillsBuild_Showcase_Presentation.pptx) — Official Showcase Pitch Deck
- [`Solution_Design_Guide_ASCE_Final.pptx`](./Solution_Design_Guide_ASCE_Final.pptx) — Complete Solution Design Guide
- [`ASCE_IBM_Submission_Package_Prime.docx`](./ASCE_IBM_Submission_Package_Prime.docx) & [`ASCE_TRL6_Engineering_Build_Progress_Report.docx`](./ASCE_TRL6_Engineering_Build_Progress_Report.docx) — Engineering & Validation Reports

---

## 4. Verified Enterprise Ecosystem Credentials
- **IBM Cloud Catalog Offering:** `ASCE GOVBEN CaaS` (`v10.0.0`, Offering ID `ac46087fe4924a7a98026981d3f0230c:o:17f34ac9-f6f2-4279-9ef3-8d91bd990d0c`, Product ID `85beaa09-c840-417e-82de-a61430d1e729`, ECCN `EAR99`, UNSPSC `43232606`)
- **Red Hat Technology Partner:** Org ID `20792660`, Product ID `6ae3354d17b7f519e40a0c89`, Container Project PID `6ae487b483fefa429257d504` (`registry.access.redhat.com/ubi9/python-311`, OpenShift Preflight `10/10 PASS`)
- **State Government Supplier:** Approved Georgia Local Government Supplier (`GA@WORK` / GPR) — `$24,500` 30-Day Micro-Pilot Validation Package
