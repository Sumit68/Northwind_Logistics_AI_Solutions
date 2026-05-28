# Northwind Expense Pre-Review — Implementation Plan

> **Note:** This file is the internal implementation plan. The grader-facing project documentation lives in [README.md](README.md).

## What the materials require

From `CANDIDATE_BRIEF.pdf`, the system is an **AI-assisted pre-review** tool (human makes final call).

| # | Capability | Persistence / quality bar |
|---|------------|---------------------------|
| 1 | Start submission for seeded or new employee | 5 employees from `employee_info.json` auto-loaded at startup |
| 2 | Upload PDF / JPG / PNG / TXT receipts | Extract structured line items per receipt |
| 3 | Per-line verdict + reasoning + **quoted** policy clause + confidence | Flagged/rejected visually distinct from compliant |
| 4 | Reviewer override + comment | Auditable, survives restart |
| 5 | Browse/filter submission history | DB-backed, not in-memory |
| 6 | Ad-hoc policy Q&A | Grounded citations; refuse out-of-scope |

**Deliverables:** GitHub repo, live URL, README, eval harness (JSON in → metrics out).

**Stack:** FastAPI + React, LLM provider-agnostic.

**Architecture:** Full specialist agent fleet (~14 TEP + SEC-301) + **policy classifier router** (invoke 1–4 agents per receipt, not all 14). LangGraph, `policy_rules/*.json`, Docling/pymupdf4llm + small LLM extraction, light RAG for policy Q&A.

---

## Architecture comparison

### Option A — RAG + single review LLM
- Cheap; good for Q&A; retrieval can miss the right §; caps better in code than embeddings alone.

### Option B — All agents on every receipt
- Strong audit story; ~14× LLM cost; irrelevant agents cause false flags; hard aggregation.

### Option C — Adopted hybrid
- **Build** ~14 policy agents; **invoke** only routed subset.
- Hand-authored policy JSON + deterministic rules for caps/tiers.
- RAG for policy chat + quote verification only.

---

## Policy router / classifier

**Flow:** Extract receipt → rule-based router (`policy_registry.json`) → optional small LLM router if ambiguous → parallel selected agents → aggregate → citation validate.

### Full agent fleet (~14)

| Agent | Policy | Role |
|-------|--------|------|
| agent_tep_001 | TEP-001 | Overview, timeliness, approvals |
| agent_tep_002 | TEP-002 | Meals & entertainment caps |
| agent_tep_003 | TEP-003 | Alcohol |
| agent_tep_004 | TEP-004 | Lodging tiers, Concur |
| agent_tep_005 | TEP-005 | Air travel |
| agent_tep_006 | TEP-006 | Ground transport |
| agent_tep_007 | TEP-007 | Receipt requirements |
| agent_tep_008 | TEP-008 | Per-diem |
| agent_tep_009 | TEP-009 | Employee grades (trip-level) |
| agent_tep_010 | TEP-010 | Corporate card |
| agent_tep_012 | TEP-012 | Gifts & entertainment |
| agent_tep_013 | TEP-013 | International travel |
| agent_tep_014 | TEP-014 | Conference |
| agent_sec_301 | SEC-301 | Travel risk |

**No agents** for noise policies (REC, HR, COC, SUS, etc.).

### policy_registry.json
- `doc_id`, `types`, `receipt_signals`, `always_with` (e.g. meal + alcohol → TEP-003).

### Router layers
1. **Rules** — vendor/category → `policy_ids[]` (free).
2. **Small LLM** — if ambiguous; structured `PolicyRoutingDecision`.

---

## Receipt extraction

```
Receipt → Docling or pymupdf4llm → text → small LLM → ExtractedReceipt JSON
```

- **USD default** when `$` or unclear.
- **confidence** from LLM; **ocr_confidence** passthrough only if module provides it (no manual formulas).
- Threshold: LLM confidence < 0.6 or ocr_confidence < 0.5 → `needs_review`.

---

## Confidence (v1 — simplified)

| Stage | Source |
|-------|--------|
| Extraction | LLM `confidence`; optional `ocr_confidence` from parser |
| Routing | LLM `routing_confidence` |
| Agents | LLM per agent |
| Final | Primary violating agent's confidence; disagree → `needs_review`, 0.5 |

**Citation check** (not confidence): quote must match policy JSON/PDF chunk.

---

## Sample submissions (dev expectations)

| Folder | Expected |
|--------|----------|
| 01_clean_denver | Largely compliant |
| 02_clean_boston_conf | Compliant; conference; premium economy OK on 6h+ segment |
| 03_dinner_over_cap | Flag — Alinea $148 > $75 dinner cap |
| 04_alcohol_solo_travel | Reject — alcohol on solo travel |
| 05_receipt_mismatch | Flag — lodging over Seattle cap; non-Concur; TEP-007 |

Meal caps (TEP-002): Breakfast $25, Lunch $35, Dinner $75.

---

## Monorepo layout

```
case_study/
├── README.md              # Grader-facing (candidate brief)
├── PLAN.md                # This file
├── backend/
│   ├── app/
│   │   ├── policy_rules/
│   │   ├── graph/         # LangGraph
│   │   ├── services/
│   │   └── llm/
├── frontend/
├── eval/
├── policies/
├── submissions/
└── docker-compose.yml
```

---

## Implementation phases

| Phase | Work |
|-------|------|
| 0 | README + decision log (ongoing) |
| 1 | policy_registry.json, policy_rules/*.json, PDF chunk index |
| 2 | DB models, seed 5 employees |
| 3 | Docling/pymupdf4llm + LLM extraction |
| 4 | LangGraph: router, agents, aggregate, citations |
| 5 | REST API + persistence |
| 6 | React UI |
| 7 | Policy Q&A + refusal |
| 8 | eval/run_eval.py |
| 9 | Deploy + README cost section |

---

## Build order

Seed employees → index policies → one receipt end-to-end → UI → overrides → history → chat → eval → deploy.

---

## Implementation todos

- [ ] README (candidate brief sections + decision log)
- [ ] Scaffold monorepo + docker-compose
- [ ] policy_registry.json + policy_rules JSON
- [ ] PDF chunk index (pgvector)
- [ ] DB + employee seed
- [ ] Receipt extractor (Docling + LLM)
- [ ] LangGraph review workflow
- [ ] API routes
- [ ] React UI
- [ ] Eval harness
- [ ] Deploy

---

## Out of scope v1

Concur integration, multi-tenant auth, approval workflow emails.
