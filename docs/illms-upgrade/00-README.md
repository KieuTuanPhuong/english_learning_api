# ILLMS Upgrade — AI-agent implementation instructions

This directory is an **ordered set of implementation briefs** for an autonomous coding agent. Each file is a self-contained work package that moves the existing `english-learning-api` (a Django + DRF MVP) toward the **Integrated Language Learning Management System (ILLMS)** described in [`../../docs.md`](../../docs.md) (repo-relative: `/Users/macbook/USTH/docs.md`).

> **Read this file first.** It states the gap, the global rules every brief obeys, the recommended build order, and how the files relate. Then implement the numbered briefs in order.

---

## What exists today vs. what docs.md wants

**Today** (`core/` single app): JWT auth, role RBAC (`IsAdmin`/`IsTeacherOrAdmin`/`IsStudent`), suspended-user enforcement (`IsActiveUser`), and CRUD for users, classes (+enrollment, lesson plans, assignments), learning modules, exercises (writing/speaking/reading/listening/quiz — with `Question`/`QuestionOption` auto-grading for receptive types), submissions, feedback, and progress. Django admin at `/admin/`, OpenAPI docs at `/api/docs/`. Mock file URLs (no real upload), **no AI**, **no real-time**.

**docs.md target** adds: an official study-materials library, AI-attributed evaluations + a submission status lifecycle, role dashboards, analytical report export, a configurable multimodal AI evaluation suite (Whisper STT + LLM grading), admin governance/audit + system monitoring, and a WebSocket real-time layer — plus closing three carried-over security gaps.

### docs.md → brief coverage

| docs.md area | RBAC rows / UCs | Brief |
|---|---|---|
| Data model alignment (`evaluations`/`submission_status`, exercise fields, NOT-NULL FKs) | §4 DBML, §5 | **01** |
| Security safeguards (register role, feedback ownership, teacher lock) | §6, UC-01, J6 | **02** |
| `study_materials` library | rows 6, 7 | **03** |
| Grading / evaluations / teacher inbox | rows 10, 11; UC-14/15; J4 | **04** |
| Dashboards + report export (`.csv`/`.pdf`) | row 16; UC-05/11/16 | **05** |
| AI evaluation suite (`ai_models`, Whisper/LLM, AI practice) | rows 12, 13; UC-09/17/18/20/21 | **06** |
| Admin ops + `system_logs` + monitoring | rows 14, 17; UC-19/20/21 | **07** |
| Real-time WebSockets (live speaking, notifications) | §1, §6 #4; UC-09 | **08** |

What docs.md describes that these briefs **deliberately mock or defer** (mirroring the repo's stated MVP stance — "mock file URLs as strings", "skip AI grading integrations"): live OpenAI Whisper / LLM calls (pluggable mock backend, brief 06), real binary audio streaming (mock string frames, brief 08), Redis + production ASGI server (in-memory dev layer, brief 08), real file/PDF generation (CSV is the MVP, PDF deferred, brief 05), live CPU/memory metrics (deferred `psutil`, brief 07). Each brief marks its stub-vs-real boundary explicitly.

---

## The briefs

| # | File | Adds | New models | Depends on |
|---|---|---|---|---|
| 01 | [01-model-foundations.md](./01-model-foundations.md) | Field/enum deltas to existing models; migration + seed conventions; docs.md-DBML → Django mapping | — | none (foundation) |
| 02 | [02-security-hardening.md](./02-security-hardening.md) | Register role allowlist; feedback ownership guard; class `teacher_id` lock | — | 01 |
| 03 | [03-study-materials-library.md](./03-study-materials-library.md) | Admin-managed study docs, read by all | `StudyMaterial` | 01 |
| 04 | [04-grading-evaluations-inbox.md](./04-grading-evaluations-inbox.md) | Submission status lifecycle, AI attribution, teacher submissions inbox | — | 01, 02 (↔06, see order) |
| 05 | [05-dashboards-and-reports.md](./05-dashboards-and-reports.md) | Role dashboards; CSV grade-report export | — | 01, 04 |
| 06 | [06-ai-evaluation-suite.md](./06-ai-evaluation-suite.md) | `AiModel` config; pluggable AI grading service; student AI practice | `AiModel` | 01, 04 |
| 07 | [07-admin-ops-and-audit.md](./07-admin-ops-and-audit.md) | Audit log + write hooks; admin activity/health; user-mgmt hardening | `SystemLog` | 01, 02, 04 |
| 08 | [08-realtime-websockets.md](./08-realtime-websockets.md) | Channels/ASGI; live speaking consumer; classroom notifications | — | 06, 04, 01 |

Each brief follows the same shape: **Goal · Why (gap) · Changes (file-by-file with copy-pasteable Django/DRF code) · Migrations · RBAC/permissions · Acceptance criteria & smoke test · Deferred**.

---

## Recommended build order

```
01  ─►  02  ─►  03
        │
        └──►  04  ─►  06        (build 04's status lifecycle first, then 06 plugs in)
               │       │
               ├──►  05         (dashboards reuse 04's ungraded/inbox queryset)
               ├──►  07         (audit hooks ride on 02's admin-only status write + 04's status)
               └────────────►  08   (real-time; speaking consumer calls 06's scorer)
```

1. **01** — apply first. It is the single source of truth for model shape; every other brief references its fields (`Submission.status`/`SubmissionStatus`, `Feedback.is_ai_generated`, `Exercise.content_text`/`created_by`) and never redefines them.
2. **02** and **03** — independent of each other; either order after 01.
3. **04** — the grading lifecycle. **Note the 04↔06 relationship:** 04 *owns* the `Submission.status` transition logic and the `is_ai_generated` semantics; 06 is the *producer* of AI feedback that trips the `AI_Graded` path. Build **04 first** (its human-grading path stands alone; the AI path can be stubbed), then **06**, which writes the `is_ai_generated=True` rows 04 reacts to. The "04 depends on 06" note in 04's header is only this producer hook — it does not block 04 from shipping.
4. **05** and **07** — after 04 (they reuse its status field / ungraded queryset).
5. **06** — before 08's speaking consumer (which calls `core.ai.service`).
6. **08** — last; the real-time layer is the heaviest infra change and consumes 06 + 04. It degrades gracefully if 06 is absent.

Each brief is independently shippable behind its own migration — you do not have to land all eight at once.

---

## Global rules (every brief obeys these)

- **Additive & contract-preserving.** A Next.js frontend consumes the current API (see [`../../UI_FLOW_BRIEF.md`](../../UI_FLOW_BRIEF.md), [`../../DJANGO_MIGRATION.md`](../../DJANGO_MIGRATION.md)). No brief renames a live table/endpoint/field or changes the login response shape `{access_token, refresh_token, token_type:"bearer"}`. docs.md names that differ from the code are **mapped onto** existing names (e.g. docs.md `evaluations` == `Feedback`, `study_materials` == new `StudyMaterial`). 01's mapping table marks every such case `RENAME-AVOIDED`.
- **Enums are `TextChoices`** (stored as varchar) — adding a member is a model edit + a normal migration. There is **no** native-PG-enum `ALTER TYPE` dance. ⚠️ [`../../REFACTOR_PLAN.md`](../../REFACTOR_PLAN.md) describes the *old retired SQLAlchemy FastAPI app* and its enum-migration procedure is **stale** — do not follow it; these briefs supersede it for the Django codebase.
- **Migrations:** `python manage.py makemigrations core && python manage.py migrate`. New models also get: a `core/admin.py` registration, a `seed_demo.py` wipe entry + seed rows, and a `SPECTACULAR_SETTINGS["TAGS"]` entry (tags added across briefs: `study-materials`, `dashboard`, `reports`, `ai`, `admin` — each declared once).
- **Wire naming:** FKs surface as `*_id` via `PrimaryKeyRelatedField(source=…)`; the `Class` FK model field is `klass`, exposed as `class_id`. List endpoints return **bare arrays** (pagination disabled).
- **Permissions:** reuse `core/permissions.py` (`IsActiveUser`, `IsAdmin`, `IsTeacherOrAdmin`, `IsStudent`) and the `_BASE`/`_perms(*extra)` helper in `core/views.py`. Suspended-user 403 **already exists** — do not re-implement it.
- **Realism policy:** anything needing an external service (Whisper, LLM, Redis, real upload) is built as a Django **interface + env-gated mock backend** (deterministic stub by default) so the API shape matches docs.md without live keys.

---

## How to use these briefs (for the implementing agent)

1. Read this README, then the brief you're implementing **and** every brief in its **Depends on** list.
2. **Verify before you cite.** Each brief grounds its code in the real tree, but the tree may have moved — `Read`/`Grep` `core/models.py`, `core/views.py`, `core/serializers.py`, `config/settings.py`, `config/urls.py`, `core/admin.py`, `core/management/commands/seed_demo.py` and confirm every referenced symbol still exists before editing.
3. Make the file-by-file changes, run the migration, update the seeder, then run the brief's **Acceptance criteria & smoke test** (curl/httpie against seeded users — all `password123`: `admin@english.app`, `*.teacher@english.app`, `*.student@english.app`).
4. Keep changes additive; if you must touch a method another brief also edits (e.g. `FeedbackViewSet.perform_create`, edited by 04 and 08), extend the single existing version rather than writing a competing one.

### Provenance & confidence

These briefs were generated against the codebase as of this writing and cross-checked for internal consistency (shared fields defined only in 01; no tag collisions; cross-links resolved; no contract-breaking renames). Brief **03** additionally passed a line-by-line accuracy re-audit against the live source. The remaining briefs are internally consistent but were **not** each independently re-audited symbol-by-symbol — so step 2 above (verify before you cite) is not optional. Treat every code block as a high-fidelity proposal, not a guaranteed-compiling patch.
