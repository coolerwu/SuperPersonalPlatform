# Model-driven Harness Design

## Objective

Use the selected `ModelDefinition` as the single source of truth for choosing
`PROMPT` or `AGENT` Harness execution. Remove mode selection from individual
requests and remove `HarnessRuntime` from the public `run_agent` call shape.

## Configuration

`ModelDefinition` gains a required, validated `mode` field with supported values
`prompt` and `agent`. Existing configuration without the field defaults to
`prompt`. The configuration API, example configuration, and model editor expose
and preserve this field.

One model definition represents one execution mode. When the same upstream model
must support both modes, configure two model definitions that may share provider,
endpoint, credentials, and upstream model name.

## Domain Design

`HarnessRequest` no longer contains `mode`. It carries only per-run input and tool
context.

`HarnessRuntime` is replaced by `AgentHarness`. `AgentHarness` is constructed once
with `AgentModelGateway`, owns the prompt and agent runners, and exposes
`run_agent(agent, request, options=None)`. It selects a runner from
`agent.model.mode`, validates options, emits checkpoints, and delegates execution.
Callers therefore do not pass a runtime object into `run_agent`.

The model gateway remains an injected dependency. It is owned indirectly by the
runner instances inside `AgentHarness`; it is not added to the `Agent` domain
object and is not created inside domain code.

## Application Flow

`AgentChatService` constructs one `AgentHarness` during service initialization.
For each request it resolves the Agent, resolves the Agent's model and tools,
builds a mode-free `HarnessRequest`, and calls
`self._harness.run_agent(...)`.

Tool availability does not select the mode. A model configured with `mode=agent`
uses the strict Agent loop even when no tools are available. A model configured
with `mode=prompt` uses direct completion even if the Agent's bound skills expose
tools; tool context is omitted because prompt mode cannot execute it.

## Validation And Errors

- Unknown model mode values fail configuration validation.
- Image capability checks remain based on `supports_images`.
- Agent mode without tools is valid and may complete through model reasoning.
- Prompt mode never dispatches tools.
- Missing model, unusable API key, invalid input, and iteration-limit behavior
  remain unchanged.

## Compatibility

Configuration without `mode` defaults to `prompt`. No migration of persisted chat
or session data is required. Existing callers must stop constructing
`HarnessRequest(mode=...)`; tests use model definitions with the desired mode.

## Verification

- Domain tests verify model-mode validation and runner selection.
- Harness tests cover prompt mode, agent mode with tools, and agent mode without
  tools.
- Application tests verify request construction no longer selects mode from tool
  availability.
- Configuration API tests verify mode read/write behavior.
- Frontend tests/build verify the model editor mode selector.
