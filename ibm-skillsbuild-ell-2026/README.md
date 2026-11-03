# OFFICIAL IBM SKILLSBUILD SUBMISSION PACKAGE (Updated: Prime Contractor End User)
**Team:** ASCE (Automated Statutory Compliance Engine) | **Track:** #4 Government & Public Services
**University:** Lincoln Tech | **Lead Developer / Inventor:** Todd Bullitt

---

## DELIVERABLE 1: 3-Minute Video Demonstration
- **Script:** Section 7 of [ASCE_Redone_Lab_Deliverables_Prime_End_User.md](file:///C:/Users/tbull/.gemini/antigravity/brain/7d25c696-523b-40b5-a385-8bb3ec38b6ab/ASCE_Redone_Lab_Deliverables_Prime_End_User.md)
- **Visuals:** prime-centered as-is and to-be slides from [ASCE_Solution_Design_Guide_IBM_SkillsBuild_Prime_End_User.pptx](file:///C:/Users/tbull/.gemini/antigravity/brain/7d25c696-523b-40b5-a385-8bb3ec38b6ab/ASCE_Solution_Design_Guide_IBM_SkillsBuild_Prime_End_User.pptx), then the terminal demo.
- **Status:** the old voiceover (`ASCE_Deliverable_1_Audio.mp3`) and pitch video still use the caseworker/survivor script. They need re-recording from the new script.

## DELIVERABLE 2: Written Problem and Solution Statement

### Problem
Federal agencies outsource disaster, Medicaid and veteran casework to prime contractors, who carry the performance risk. Primes apply changing statutory rules by hand, so superseded rules, duplicate document requests, missed 90-day deadlines (44 CFR § 206.115(e)), ignored inspector findings and missed protected-status flags are found **after** the decision. By then SLA withholds, award-fee reductions and CPARS damage are accruing. The same failures delay aid to survivors and veterans and add to improper payments that taxpayers absorb.

**Primary end user:** Prime Contractor Program Director (`Marcus Reyes` persona, validated with federal prime and state procurement stakeholders).
**Stakeholders:** agency and COR, caseworkers, applicants, OIG/GAO/Congress, taxpayers.

### Solution
ASCE is deterministic compliance middleware that sits between a proposed action and the case system of record inside the prime's delivery stack. It intercepts each action before finalization, applies the rule in force on the disaster date, enforces statutory clocks, blocks redundant document requests, reconciles decisions with inspector findings, flags protected status, and issues a SHA-256 tamper-evident receipt. It integrates by REST/JSON or legacy interfaces and does not replace the agency's system.

- **Live Working MVP Endpoint (IBM Cloud Code Engine):** `https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/`
- **IBM Cloud Catalog Offering:** `ASCE GOVBEN CaaS v10.0.0` (Offering ID: `ac46087fe4924a7a98026981d3f0230c:o:17f34ac9-f6f2-4279-9ef3-8d91bd990d0c`)

### Hills
1. Fewer penalties and less rework (correct rule, no redundant requests).
2. Deadline compliance before breach.
3. Audit-ready proof of compliance for the COR, OIG and CPARS.

## WEEK 8 MILESTONE (`Final Build Push`): Refinement Based on Feedback & User Validation
Per the Week 8 milestone rubric (*"🔄 Refine based on feedback: Fix issues and improve the solution using user feedback"* and *"✅ Complete technical implementation and user validation"*), the following real-world feedback loops and engineering refinements were completed between Week 6 and Week 8:
1. **Mentor & Enterprise Persona Pivot (Coach Denis Feedback):** Pivoted the primary user persona from an individual agency caseworker to the **Federal Prime Contractor Program Director (`Marcus Reyes`)** who owns SLA withhold risk, award-fee scorecards, and CPARS ratings, while preserving downstream protection for disaster survivors, veterans, and taxpayers.
2. **Tier-1 Federal Prime Stakeholder Validation (SAIC):** Shared the updated Prime Contractor Teaming Dossier and deterministic pre-finalization gate architecture with **Vernon Isaacs** (SAIC Enterprise Leader, `Vernon.l.isaacs@saic.com`), who reviewed the model and initiated internal routing toward SAIC's delivery leadership (`Shawn Olone` and `Chris Barnes`).
3. **State Agency Procurement Validation (Georgia DCH & GEMA/HS):** Completed official State of Georgia supplier onboarding (`GA@WORK` Marketplace) and delivered formal 30-day synthetic micro-pilot validation proposals (`$24,500` sole-source threshold) to the **Georgia Department of Community Health (DCH)** (Medicaid Redetermination Integrity) and **Georgia Emergency Management & Homeland Security Agency (GEMA/HS)** (FEMA PA/IA 44 CFR Audit Readiness).
4. **Embedded Edge Persistence Validation & V2 Architectural Refinement (McObject):** Evaluated McObject's `EdgeMemory` C prototype natively on **64-bit ARM64 Red Hat Enterprise Linux 9 (`aarch64-redhat-linux`)** and collaborated directly with McObject CEO **Steve Graves** (`steve@mcobject.com`) to define three production refinements for Version 2:
   - *Refinement 1:* Pre-Commit Cryptographic Receipt Hook (`SHA-256 / HMAC` over `bucket + key + payload + observed_timestamp`).
   - *Refinement 2:* Zero-Copy Fixed-Width Binary Struct Serialization (80-byte COBOL records and tactical edge telemetry).
   - *Refinement 3:* Strict Minimum-Similarity Cutoff on embedding recall queries so advisory context never surfaces low-confidence matches.

## DELIVERABLE 3: Written Statement on Technology
- Deterministic Python state machine (17 statutory flag types, 44 whitelisted CFR sections, 7 programs); rules always override AI.
- IBM watsonx.ai Granite 3 generates plain-language guidance only; Agent 4 validates citations against a whitelist.
- Containerized on Red Hat UBI 9 (Preflight 10/10, Red Hat Partner Connect Product ID `6ae3354d17b7f519e40a0c89` at 28/29 validation), deployed to IBM Cloud Code Engine / OpenShift; built with IBM Bob and watsonx Orchestrate.
- Evidence: TRL-6 sandbox validation (six modules on a compound-anomaly payload) and 5,000 synthetic files in 1.79 s (`2,786 claims/sec`, `0.36 ms` mean latency).
- **Data boundary:** synthetic data only; no real citizen PII.
- **Verified Native ARM64 EdgeMemory Prototype Benchmarks (Completed Nov 3, 2026):** Executed McObject's `EdgeMemory` prototype natively on **ARM64 RHEL 9 (`AlmaLinux 9.7 aarch64`, GCC 11.5.0)**:
  - **Unit Test Suite (`edgememory_tests`):** `100% PASS` across `basic_write_and_recall`, `persistence_roundtrip`, `retention_prune`, and `stress_and_corrupt_load`.
  - **Write Throughput:** **`135,430 ops/sec`** (`10,000` records written in `73.84 ms`, `0.0074 ms/op`).
  - **In-Memory Recall Throughput:** **`17,060 queries/sec`** (`2,000` top-5 queries in `117.23 ms`, `0.0586 ms/query`, deterministic checksum `66295416`).
  - **Persistent Reload (`ext4` disk) Recall Throughput:** **`9,160 queries/sec`** (`2,000` top-5 queries in `218.33 ms`, `0.1092 ms/query`, identical deterministic checksum `66295416`).
  - **Architectural Separation:** ASCE's statutory adjudication path remains 100% deterministic; `EdgeMemory` serves as the sub-millisecond tactical audit ledger and advisory recall cache for disconnected/air-gapped field deployments.

## DELIVERABLE 4: Bob Task Session Reports
Exported to `IBM_Experiential_Lab_2026/bob_sessions/` (unchanged).
