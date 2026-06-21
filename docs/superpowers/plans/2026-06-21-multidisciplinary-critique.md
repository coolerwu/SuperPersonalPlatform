# Multidisciplinary Critique Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent multidisciplinary critique workspace that runs transient Prompt Agents concurrently through the existing Harness and consolidates their structured challenges.

**Architecture:** Domain factories create valid Prompt or Agent `HarnessRequest` values from already-bound Agents. A new application service resolves the configured prompt model, persists discipline/run data, fans transient prompt requests out with `asyncio.gather`, and runs a final judge request. Authenticated REST/WebSocket routes expose the service, while a focused React page implements the selected matrix design.

**Tech Stack:** Python 3.11+, FastAPI/WebSocket, existing Harness and LangChain model runner, JSON workspace persistence, React 18, Vitest/Testing Library, CSS.

---

### Task 1: Domain Harness Request Factories

**Files:**
- Modify: `server/domain/harness/contracts.py`
- Modify: `server/app/agent_chat_service.py`
- Test: `server/tests/test_agent_harness.py`
- Test: `server/tests/test_agent_chat.py`

- [ ] **Step 1: Write failing factory tests**

Add tests that call the wished-for APIs and assert their legal shapes:

```python
def test_prompt_request_factory_omits_tool_context() -> None:
    request = HarnessRequest.for_prompt(agent=make_agent(), content=" challenge ")
    assert request.content == "challenge"
    assert request.tool_names == ()
    assert request.tool_registry is None
    assert request.tool_runtime is None


def test_agent_request_factory_requires_registry_for_tools() -> None:
    with pytest.raises(ValueError, match="tool_registry"):
        HarnessRequest.for_agent(
            agent=make_agent(HarnessMode.AGENT),
            content="inspect",
            tool_names=("first",),
        )
```

Add an application test that patches `run_agent`, calls `AgentChatService.chat()`,
and asserts the captured request is produced through `for_prompt()` for a prompt
model and `for_agent()` for an agent model.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/pytest server/tests/test_agent_harness.py server/tests/test_agent_chat.py -q
```

Expected: FAIL because `HarnessRequest.for_prompt` and `for_agent` do not exist.

- [ ] **Step 3: Implement the domain factories**

Add class methods that trim and validate content, validate `max_iterations > 0`,
and enforce consistent tool context:

```python
@classmethod
def for_prompt(cls, *, agent, content, images=(), on_checkpoint=None):
    content = content.strip()
    if not content and not images:
        raise ValueError("消息内容不能为空")
    return cls(agent=agent, content=content, images=images, on_checkpoint=on_checkpoint)

@classmethod
def for_agent(
    cls, *, agent, content, images=(), tool_names=(), tool_registry=None,
    tool_runtime=None, on_checkpoint=None, max_iterations=60,
):
    content = content.strip()
    if not content and not images:
        raise ValueError("消息内容不能为空")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be greater than zero")
    if tool_names and tool_registry is None:
        raise ValueError("tool_registry is required when tool_names are provided")
    if not tool_names and (tool_registry is not None or tool_runtime is not None):
        raise ValueError("tool context requires tool_names")
    return cls(
        agent=agent, content=content, images=images, tool_names=tool_names,
        tool_registry=tool_registry, tool_runtime=tool_runtime,
        on_checkpoint=on_checkpoint, max_iterations=max_iterations,
    )
```

Refactor both `AgentChatService.chat()` and `run_with_tool_runtime()` to delegate
construction to these factories without changing model-driven mode selection.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 2: Critique Domain And Persistence

**Files:**
- Create: `server/domain/critique.py`
- Create: `server/app/critique_service.py`
- Create: `server/tests/test_critique_service.py`

- [ ] **Step 1: Write failing discipline persistence tests**

Define tests against `CritiqueService(tmp_path, fake_agent_service)`:

```python
def test_create_discipline_persists_user_scope_and_default(tmp_path) -> None:
    service = CritiqueService(tmp_path, FakeAgentService())
    created = service.create_discipline(
        name="经济学",
        known_scope="微观决策与机会成本",
        critique_focus="成本、激励与替代方案",
        default_enabled=True,
    )
    assert service.list_disciplines() == (created,)
    assert json.loads((tmp_path / "critique" / "disciplines.json").read_text())[0]["name"] == "经济学"
```

Cover update, delete, duplicate/empty validation, stable generated ids, and atomic
replacement (no leftover `.tmp` file).

- [ ] **Step 2: Run persistence tests and verify RED**

Run:

```bash
.venv/bin/pytest server/tests/test_critique_service.py -q
```

Expected: collection/import failure because the critique modules do not exist.

- [ ] **Step 3: Implement domain records and atomic repository behavior**

Create frozen dataclasses `CritiqueDiscipline`, `CritiqueCell`,
`CritiqueDisciplineResult`, `CritiqueJudgment`, and `CritiqueRun`. Store
`disciplines.json` plus `runs/{uuid}.json`; serialize through explicit dictionaries,
write UTF-8 JSON to `*.tmp`, then `replace()` the target.

- [ ] **Step 4: Verify persistence tests GREEN**

Run the command from Step 2. Expected: all persistence cases pass.

- [ ] **Step 5: Write failing orchestration tests**

Use a fake request factory and patch `server.app.critique_service.run_agent`:

```python
async def fake_run_agent(request):
    active += 1
    max_active = max(max_active, active)
    await asyncio.sleep(0)
    active -= 1
    return RESPONSES[request.agent.definition.id]

result = asyncio.run(service.run_critique("是否辞职？", (economics.id, psychology.id)))
assert max_active == 2
assert [item.discipline_id for item in result.results] == [economics.id, psychology.id]
assert result.judgment.weakest_assumption
```

Add cases for fenced JSON, invalid JSON as one failed discipline, consolidation
with successful results only, all disciplines failing, explicit prompt-model
rejection, saved discipline snapshots, and retrying one failed discipline.

- [ ] **Step 6: Run orchestration tests and verify RED**

Run the command from Step 2. Expected: FAIL because orchestration is absent.

- [ ] **Step 7: Implement transient Prompt Agent orchestration**

Add an `AgentChatService.bind_prompt_agent(system_prompt, model_id=None, id, name)`
application method that loads the platform, resolves the explicit/default model,
requires `model.mode is HarnessMode.PROMPT`, validates the key, and returns a bound
`Agent`. `CritiqueService` calls `HarnessRequest.for_prompt()` for each discipline
and judge, uses `asyncio.gather(..., return_exceptions=True)`, preserves selected
discipline order, parses fixed JSON keys, emits status callbacks, and persists the
run after every terminal state.

- [ ] **Step 8: Verify orchestration tests GREEN**

Run the command from Step 2. Expected: all critique service tests pass.

### Task 3: Authenticated Critique API

**Files:**
- Create: `server/adapter/critique_routes.py`
- Modify: `server/adapter/dependencies.py`
- Modify: `server/infrastructure/fastapi_app.py`
- Create: `server/tests/test_critique_routes.py`

- [ ] **Step 1: Write failing REST and WebSocket route tests**

Build a test app with auth and critique routers. Cover:

```python
assert client.get("/api/critique/disciplines").status_code == 401
login(client)
created = client.post("/api/critique/disciplines", json=PAYLOAD)
assert created.status_code == 201
with client.websocket_connect("/api/critique/runs/connect") as websocket:
    websocket.send_json({"type": "run", "question": "是否辞职？", "discipline_ids": [created.json()["discipline"]["id"]]})
    assert websocket.receive_json()["type"] == "run_started"
```

Assert progressive `discipline_status`, `judgment_status`, `run_completed`, safe
validation errors, run list/detail, delete/update, and retry messages.

- [ ] **Step 2: Run route tests and verify RED**

Run:

```bash
.venv/bin/pytest server/tests/test_critique_routes.py -q
```

Expected: import failure because the router does not exist.

- [ ] **Step 3: Implement router and dependency wiring**

Create authenticated `/api/critique` CRUD/history routes and
`/api/critique/runs/connect`. Reuse `is_authenticated_request` before WebSocket
accept. Add `critique_service` to `AppContainer`, construct it from the active
workspace and `AgentChatService`, include its router before proxy fallbacks, and map
domain validation to HTTP 400/404 without returning exception reprs.

- [ ] **Step 4: Verify route tests GREEN and run backend regression**

Run:

```bash
.venv/bin/pytest server/tests/test_critique_routes.py server/tests/test_agent_harness.py server/tests/test_agent_chat.py -q
```

Expected: all selected tests pass.

### Task 4: Matrix Frontend

**Files:**
- Create: `web/src/CritiquePage.jsx`
- Modify: `web/src/main.jsx`
- Modify: `web/src/styles.css`
- Modify: `web/src/main.test.jsx`
- Copy: `docs/design-references/multidisciplinary-critique-matrix.png`

- [ ] **Step 1: Preserve the selected visual target**

Copy the selected ImageGen result
`/Users/wulang/.codex/generated_images/019ee93f-c3e2-7ed3-98b4-9abc5c82b8c6/exec-c1e71775-5d69-4b39-a2cf-ec4fb732819e.png`
to the documented design-reference path.

- [ ] **Step 2: Write failing frontend behavior tests**

Extend the existing mocked `fetch` and `WebSocket` setup to assert:

```jsx
expect(screen.getByRole("button", { name: "多维批判" })).toBeInTheDocument();
fireEvent.click(screen.getByRole("button", { name: "多维批判" }));
expect(screen.getByPlaceholderText("输入你想被质疑的问题...")).toBeInTheDocument();
expect(screen.getByRole("columnheader", { name: "核心假设" })).toBeInTheDocument();
```

Add tests for opening the discipline editor, saving all four fields, default
selection versus temporary deselection, sending a run message, progressive result
events, judge output, partial failure/retry, and history selection.

- [ ] **Step 3: Run frontend tests and verify RED**

Run:

```bash
cd web && npm test -- --run
```

Expected: FAIL because the route and page do not exist.

- [ ] **Step 4: Implement the focused React page**

Export `CritiquePage({ onUnauthorized })` from its own module. Keep constants and
subcomponents at module scope, fetch discipline/history data in parallel, derive
selected defaults without an effect, use functional state updates for WebSocket
events, and close the socket on unmount. Implement the question composer, matrix,
judge panel, collapsible discipline library, editor dialog, run history, failed
state, retry, loading, empty, and unauthorized behavior.

Add the `/critique` sidebar item and route in `main.jsx`. Use existing Lucide icons
and theme tokens. CSS must match the selected reference at desktop width and turn
each table row into a labeled stacked group below the existing mobile breakpoint.

- [ ] **Step 5: Verify frontend tests GREEN and build**

Run:

```bash
cd web && npm test -- --run && npm run build
```

Expected: tests pass and Vite build exits zero.

### Task 5: Architecture Documentation And Full Verification

**Files:**
- Modify: `docs/project-architecture.md`
- Update: `docs/superpowers/plans/2026-06-21-multidisciplinary-critique.md`

- [ ] **Step 1: Update architecture memory**

Document the Domain `HarnessRequest` factories, application-level transient prompt
binding, critique REST/WebSocket routes, workspace files, partial-failure behavior,
and `/critique` page. Do not add secrets or local model credentials.

- [ ] **Step 2: Run complete automated verification**

Run:

```bash
.venv/bin/pytest -q
cd web && npm test -- --run && npm run build
git diff --check
```

Expected: zero test failures, successful build, and no whitespace errors.

- [ ] **Step 3: Run visual QA against the selected reference**

Start `./run-dev.sh`, open `http://127.0.0.1:8888/critique` in the in-app Browser at
1440x1024, populate representative matrix data through the functional UI, capture
the implementation, compare it beside the selected reference, and save
`design-qa.md`. Fix all P0-P2 findings and repeat until the file says
`final result: passed`.

- [ ] **Step 4: Execute the repository commit workflow**

Read and execute `.codex/skills/project-commit/SKILL.md`. Recheck all uncommitted
files, update required operating docs, commit all changes, push the current branch,
then SSH to production and run the documented pull plus systemd restart sequence.
