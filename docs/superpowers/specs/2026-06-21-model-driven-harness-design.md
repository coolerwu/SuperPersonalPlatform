# Model-driven Harness Design

## Objective

Use the selected `ModelDefinition` as the single source of truth for choosing
`PROMPT` or `AGENT` Harness execution. Remove mode selection from individual
requests and reduce the public call shape to `run_agent(agent, request, options)`.

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

`run_agent(agent, request, options=None)` is stateless. It creates a
`ModelRunner` from `agent.model`, then creates `PromptRunner` or `AgentRunner`
according to `agent.model.mode`. There is no runtime container, runner mapping,
gateway argument, or model client stored on `Agent`.

`ModelRunner` owns one `ModelDefinition` and resolves the concrete LangChain
provider internally. Mode runners call it without repeatedly passing the model.

## Application Flow

`AgentChatService` has no model-client constructor dependency. For each request it
resolves the Agent, model and applicable tools, builds a mode-free
`HarnessRequest`, and calls `run_agent(...)`.

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
