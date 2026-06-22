# Multi-turn Critique Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the multidisciplinary critique page from a one-shot run into a persistent, multi-turn chat that reruns the same discipline panel with prior context on every follow-up.

**Architecture:** Keep `/api/critique/runs` as the compatibility boundary, but evolve each persisted run into a conversation containing ordered critique turns. Each turn stores its question, discipline results, judgment, status, and timestamps; legacy run files are read as a single-turn conversation. The React page renders those turns as a chronological message stream and sends `follow_up` WebSocket messages against the active conversation.

**Tech Stack:** Python 3.12, FastAPI WebSockets, frozen dataclasses, atomic JSON persistence, React 18, Vitest, Testing Library, existing Lucide icon set, CSS custom properties.

---

### Task 1: Define Multi-turn Domain And Service Behavior

**Files:**
- Modify: `server/domain/critique.py`
- Modify: `server/app/critique_service.py`
- Test: `server/tests/test_critique_service.py`

- [ ] **Step 1: Write the failing follow-up persistence test**

Add a test that creates an initial run, calls `follow_up(run.id, "如果预算只有两万元，先验证什么？")`, and asserts:

```python
assert conversation.title == "我是否应该辞职做自己的产品？"
assert [turn.question for turn in conversation.turns] == [
    "我是否应该辞职做自己的产品？",
    "如果预算只有两万元，先验证什么？",
]
assert service.get_run(conversation.id) == conversation
```

Capture the second-round prompt passed to each discipline and assert that it contains both the previous user question and previous judgment, proving that the follow-up is contextual rather than a new isolated run.

- [ ] **Step 2: Run the service test and verify RED**

Run:

```bash
.venv/bin/pytest server/tests/test_critique_service.py::test_follow_up_reuses_saved_disciplines_and_persists_context -q
```

Expected: fail because `CritiqueService.follow_up` and `CritiqueRun.turns` do not exist.

- [ ] **Step 3: Add the turn model and minimal follow-up implementation**

Add the immutable turn model:

```python
@dataclass(frozen=True)
class CritiqueTurn:
    id: str
    question: str
    results: tuple[CritiqueDisciplineResult, ...]
    judgment: CritiqueJudgment | None
    status: str
    created_at: str
    updated_at: str
```

Extend `CritiqueRun` with `title` and `turns`, then extract the existing execution body into a private helper that accepts the saved discipline snapshots plus prior turns. `run_critique` creates turn one; `follow_up` appends a turn using the existing run's discipline snapshots and model id.

- [ ] **Step 4: Add legacy JSON compatibility coverage**

Write a test that stores the current single-run JSON shape without `title` or `turns`, loads it through `get_run`, and asserts one synthesized turn plus the original question as title.

- [ ] **Step 5: Run service tests and verify GREEN**

Run:

```bash
.venv/bin/pytest server/tests/test_critique_service.py -q
```

Expected: all critique service tests pass.

### Task 2: Extend The WebSocket Contract

**Files:**
- Modify: `server/adapter/critique_routes.py`
- Test: `server/tests/test_critique_routes.py`

- [ ] **Step 1: Write the failing WebSocket follow-up test**

Extend the route test to send:

```python
websocket.send_json({
    "type": "follow_up",
    "run_id": completed["id"],
    "question": "预算有限时先验证哪一个假设？",
})
```

Assert the next `run_completed` event has two turns and retains the original title.

- [ ] **Step 2: Run the route test and verify RED**

Run:

```bash
.venv/bin/pytest server/tests/test_critique_routes.py::test_critique_run_websocket_supports_contextual_follow_up -q
```

Expected: fail with the unsupported message type response.

- [ ] **Step 3: Route `follow_up` and make retry turn-aware**

Allow `run`, `follow_up`, and `retry`. Route follow-ups to:

```python
await service.follow_up(
    str(raw.get("run_id") or ""),
    str(raw.get("question") or ""),
    on_event=send_event,
)
```

Pass an optional `turn_id` for retries so a visible failed expert response can retry the correct turn; preserve the existing behavior by defaulting to the latest turn.

- [ ] **Step 4: Run route tests and verify GREEN**

Run:

```bash
.venv/bin/pytest server/tests/test_critique_routes.py -q
```

Expected: all critique route tests pass.

### Task 3: Build The Mainline Conversation UI

**Files:**
- Modify: `web/src/CritiquePage.jsx`
- Modify: `web/src/CritiquePage.test.jsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Write the failing multi-turn UI test**

Update the WebSocket fixture to emit a run containing two turns. Assert that the page renders both user questions, both judge responses, the conversation list, and a composer labelled for follow-up. Submit from the active conversation and assert:

```javascript
expect(socket.sent.at(-1)).toEqual({
  type: "follow_up",
  run_id: "r-1",
  question: "预算有限时先验证什么？"
});
```

- [ ] **Step 2: Run the frontend test and verify RED**

Run:

```bash
npm test -- --run CritiquePage.test.jsx
```

from `web/`.

Expected: fail because the current page renders a matrix and always sends `type: "run"`.

- [ ] **Step 3: Implement conversation normalization and state transitions**

Add a small compatibility normalizer that maps a legacy run to one turn. Use `activeRun.turns` as the message stream. On first submit, send `run`; on later submit, send `follow_up`. Clear the composer after dispatch without overwriting historical questions.

- [ ] **Step 4: Recreate visual option 1**

Replace the horizontal history strip and matrix-first workspace with:

```text
conversation rail | chronological critique thread | discipline/context inspector
                                      stable composer at the bottom
```

Keep the existing global app sidebar, colors, typography, radius tokens, discipline editor, selection controls, failure retry, connection indicator, and responsive behavior. Expert analyses remain collapsible within each assistant turn, with the integrated judgment always visible.

- [ ] **Step 5: Run frontend tests and verify GREEN**

Run:

```bash
npm test -- --run CritiquePage.test.jsx
npm run build
```

Expected: CritiquePage tests pass and Vite production build exits zero.

### Task 4: Document, Verify, And Compare The Rendered Result

**Files:**
- Modify: `docs/project-architecture.md`
- Create: `design-qa.md`

- [ ] **Step 1: Update architecture memory**

Document that critique run files now persist ordered turns, that the WebSocket accepts `follow_up`, that prior turns are supplied to every discipline and judge, and that legacy one-shot files are synthesized as one-turn conversations.

- [ ] **Step 2: Run focused and full automated verification**

Run:

```bash
.venv/bin/pytest server/tests/test_critique_service.py server/tests/test_critique_routes.py -q
cd web && npm test -- --run CritiquePage.test.jsx && npm run build
.venv/bin/pytest -q
cd web && npm test
```

Expected: every command exits zero with no failed tests.

- [ ] **Step 3: Run browser interaction and visual QA**

Start `./run-dev.sh`, open `http://127.0.0.1:8888/critique` at `1440x1024`, confirm page identity, no error overlay, clean console, conversation switching, first submit, follow-up submit, and stable composer behavior. Capture the implementation and combine it side by side with the selected ImageGen source before judging.

- [ ] **Step 4: Save the blocking QA report**

Write `design-qa.md` with the source image path, implementation screenshot path, viewport, state, full-view and focused comparison evidence, findings, patches, and exactly one final line:

```text
final result: passed
```

Use `blocked` instead if any actionable P0/P1/P2 issue remains.

- [ ] **Step 5: Execute the repository commit workflow**

Read and follow `.codex/skills/project-commit/SKILL.md`, include all current uncommitted changes per repository preference, run its required checks, commit, push, then SSH to production and run the documented pull and service restart sequence.
