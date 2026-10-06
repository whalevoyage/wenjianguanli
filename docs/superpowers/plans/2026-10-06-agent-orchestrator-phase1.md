# Agent Orchestrator Phase 1 Implementation Plan

> This plan is implemented inline in the current session. Each task is test-first and must preserve the existing local safety gates.

**Goal:** Upgrade Atlas Agent from keyword intent matching into a safer intent-and-plan orchestrator with structured clarification, local context, and a unified plan contract.

**Architecture:** Keep local rules as the zero-token fast path. Add structured answer, clarify, and plan results. DeepSeek remains a planner and general assistant; it never receives direct filesystem authority. Existing backend services remain the only execution boundary.

**Tech Stack:** Python, FastAPI, SQLite-backed memory, Vue 3, existing DeepSeek Chat Completions client.

## Global Constraints

- Do not add runtime dependencies.
- Do not allow the model to execute filesystem operations directly.
- Preserve readonly / standard / full permission gates.
- Preserve preview-before-write behavior for cleanup and migration.
- Local-recognized file tasks must remain zero-token.
- Existing API response fields remain backward compatible.

## Task 1: Structured clarification contract

Files: backend/services/agent.py, tests/test_agent_queries.py

- Add a clarify response type with question, missing, and suggestions.
- Return clarification for ambiguous migration, backup, scan, and cleanup requests instead of fabricating paths.
- Keep existing plan response fields unchanged for recognized tasks.

## Task 2: Local context tools

Files: backend/services/agent.py, backend/services/overview.py, tests/test_agent_queries.py

- Expose safe read-only context helpers for disk overview and task summary.
- Allow Agent to answer local-state questions without model calls.
- Never expose secrets or unrestricted directory listings through the Agent response.

## Task 3: Planner prompt and validation

Files: backend/services/agent.py, tests/test_api_validation.py

- Align normal and streaming prompts.
- Require the model to choose between answer, clarify, and plan.
- Validate action, parameters, path requirements, and write risk after model output.
- Convert malformed or unsafe output into a safe clarification or none result.

## Task 4: Frontend rendering

Files: frontend/src/views/Agent.vue

- Render clarification cards with selectable suggestions.
- Render ordinary answers without a fake file-operation warning.
- Keep plan cards, confirmation cards, recipes, memory, and permission controls working.

## Task 5: Verification

- Run Agent unit/API tests.
- Run full Python tests.
- Run frontend production build.
- Run existing Agent UI audit.
- Run the backend smoke test with the current server.
