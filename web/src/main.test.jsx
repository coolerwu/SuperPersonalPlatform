import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, test, vi, expect } from "vitest";

function response(data) {
  return { ok: true, json: async () => data };
}

const CONFIG_YAML = [
  "auth:",
  '  token: "secret-token"',
  "server:",
  '  host: "0.0.0.0"',
  "  port: 8888",
  "browser:",
  '  proxy: "http://127.0.0.1:7890"',
  "  timeout_ms: 60000",
  "llm:",
  '  default_model_id: "default"',
  "  models:",
  '    - id: "default"',
  '      name: "默认模型"',
  '      provider: "openai_compatible"',
  '      base_url: "https://api.openai.com/v1"',
  '      api_key: "key"',
  '      model: "gpt-4o-mini"',
  "      temperature: 0.7",
  "      supports_images: false",
  "nutstore:",
  "  enabled: false",
  '  base_url: "https://dav.jianguoyun.com/dav/"',
  '  username: ""',
  '  password: ""',
  '  root_path: "/"',
  "channels:",
  "  wechat_personal:",
  "    enabled: false",
  "    accounts: []",
  "agents:",
  "  definitions:",
  '    - id: "assistant"',
  '      name: "默认助手"',
  '      system_prompt: "你是一个运行在后端的 DeepAgent。"',
  '      model_id: "default"',
  "      context_ids: []",
  "      deepagent:",
  "        max_iterations: 60",
  '        name: ""',
  "        debug: false",
  "        use_longterm_memory: true",
  "        tools: []",
  "        interrupt_on: []",
  "        middleware: []",
  "        subagents: []",
  '        response_format: ""',
  '        context_schema: ""',
  "        checkpointer: false",
  '        store: ""',
  '        cache: ""',
].join("\n");

const INDENTLESS_SEQUENCE_CONFIG_YAML = [
  "auth:",
  '  token: "secret-token"',
  "server:",
  '  host: "0.0.0.0"',
  "  port: 8888",
  "browser:",
  '  proxy: ""',
  "  timeout_ms: 60000",
  "llm:",
  '  default_model_id: "ds-pro"',
  "  models:",
  '  - id: "ds-pro"',
  '    name: "DeepSeek Pro"',
  '    provider: "openai_compatible"',
  '    base_url: "https://api.deepseek.com"',
  '    api_key: "key"',
  '    model: "deepseek-v4-pro"',
  "    temperature: 0.6",
  "    supports_images: false",
  "nutstore:",
  "  enabled: false",
  '  base_url: "https://dav.jianguoyun.com/dav/"',
  '  username: ""',
  '  password: ""',
  '  root_path: "/"',
  "channels:",
  "  wechat_personal:",
  "    enabled: false",
  "    accounts:",
  '    - id: "main"',
  '      name: "主账号"',
  '      default_agent_id: "assistant"',
  "      auto_start: false",
  "agents:",
  "  definitions:",
  '  - id: "assistant"',
  '    name: "默认助手"',
  '    system_prompt: "你是一个运行在后端的 DeepAgent。"',
  '    model_id: "ds-pro"',
  "    context_ids: []",
].join("\n");

const TWO_PROVIDER_CONFIG_YAML = [
  "auth:",
  '  token: "secret-token"',
  "server:",
  '  host: "0.0.0.0"',
  "  port: 8888",
  "browser:",
  '  proxy: ""',
  "  timeout_ms: 60000",
  "llm:",
  '  default_model_id: "primary"',
  "  models:",
  '    - id: "primary"',
  '      name: "Primary"',
  '      provider: "openai_compatible"',
  '      base_url: "https://api.primary.test"',
  '      api_key: "key"',
  '      model: "primary-model"',
  "      temperature: 0.7",
  "      supports_images: false",
  '    - id: "backup"',
  '      name: "Backup"',
  '      provider: "openai_compatible"',
  '      base_url: "https://api.backup.test"',
  '      api_key: "key"',
  '      model: "backup-model"',
  "      temperature: 0.7",
  "      supports_images: false",
  "nutstore:",
  "  enabled: false",
  '  base_url: "https://dav.jianguoyun.com/dav/"',
  '  username: ""',
  '  password: ""',
  '  root_path: "/"',
  "channels:",
  "  wechat_personal:",
  "    enabled: false",
  "    accounts: []",
  "agents:",
  "  definitions:",
  '    - id: "assistant"',
  '      name: "默认助手"',
  '      system_prompt: "你是一个运行在后端的 DeepAgent。"',
  '      model_id: "primary"',
  "      context_ids: []",
].join("\n");

let scrollHeightDescriptor;

async function flushReact() {
  for (let index = 0; index < 6; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

beforeEach(() => {
  vi.resetModules();
  vi.useRealTimers();
  scrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
  document.body.innerHTML = '<div id="root"></div>';
  window.history.replaceState({}, "", "/agents");
  global.fetch = vi.fn(async (url, options = {}) => {
    if (String(url).endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (String(url).endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    return response({});
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  if (scrollHeightDescriptor) {
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", scrollHeightDescriptor);
  } else {
    delete HTMLElement.prototype.scrollHeight;
  }
});

test("renders the DeepAgent console shell", async () => {
  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("DeepAgent")).toBeInTheDocument();
  expect((await screen.findAllByText("Runs")).length).toBeGreaterThan(1);
  expect(await screen.findByText("workspace/runs/index.json")).toBeInTheDocument();
  expect(await screen.findByText("配置")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Providers/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Agents/ })).not.toBeInTheDocument();
  expect(await screen.findByText("微信")).toBeInTheDocument();
});

test("keeps run details stable when index polling returns only summaries", async () => {
  vi.useFakeTimers();
  window.history.replaceState({}, "", "/runs");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({
        runs: [
          {
            run_id: "run_20260820T070102_c6714e332e",
            agent_id: "default",
            status: "completed",
            created_at: "2026-08-20T07:01:02Z",
            updated_at: "2026-08-20T07:01:09Z",
          },
        ],
      });
    }
    if (path.endsWith("/api/runs/run_20260820T070102_c6714e332e/events")) {
      return response({
        events: [
          { seq: 1, type: "queued", created_at: "2026-08-20T07:01:02Z", payload: { message: "run queued" } },
          { seq: 2, type: "completed", created_at: "2026-08-20T07:01:09Z", payload: { message: "run completed" } },
        ],
      });
    }
    if (path.endsWith("/api/runs/run_20260820T070102_c6714e332e")) {
      return response({
        run_id: "run_20260820T070102_c6714e332e",
        agent_id: "default",
        input: { agent_id: "default", source: "wechat", created_at: "2026-08-20T07:01:02Z" },
        state: { status: "completed", seq: 2 },
        result: { status: "completed", content: "稳定结果内容" },
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  expect(screen.getByText("稳定结果内容")).toBeInTheDocument();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_100);
  });

  expect(screen.getByText("稳定结果内容")).toBeInTheDocument();
  expect(screen.queryByText("unknown")).not.toBeInTheDocument();
});

test("shows streaming partial output while a run is active", async () => {
  window.history.replaceState({}, "", "/runs");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({
        runs: [
          {
            run_id: "run_streaming",
            agent_id: "default",
            status: "running",
            created_at: "2026-08-20T07:01:02Z",
            updated_at: "2026-08-20T07:01:09Z",
          },
        ],
      });
    }
    if (path.endsWith("/api/runs/run_streaming/events")) {
      return response({
        events: [
          { seq: 1, type: "running", created_at: "2026-08-20T07:01:02Z", payload: { message: "DeepAgent started" } },
          { seq: 2, type: "assistant_delta", created_at: "2026-08-20T07:01:03Z", payload: { delta: "正在回答" } },
        ],
      });
    }
    if (path.endsWith("/api/runs/run_streaming")) {
      return response({
        run_id: "run_streaming",
        agent_id: "default",
        input: { agent_id: "default", source: "wechat", created_at: "2026-08-20T07:01:02Z" },
        state: { status: "running", seq: 2 },
        partial: { status: "streaming", content: "正在回答" },
        result: null,
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  expect(screen.getByText("正在生成")).toBeInTheDocument();
  expect(screen.getByText("正在回答")).toBeInTheDocument();
  expect(screen.getByText("workspace/runs/run_streaming/partial.json")).toBeInTheDocument();
});

test("chat page sends a message and renders the streaming assistant bubble", async () => {
  window.history.replaceState({}, "", "/chat");
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", content: CONFIG_YAML });
    }
    if (path.endsWith("/api/chat/session")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 0 },
        messages: [],
      });
    }
    if (path.endsWith("/api/chat/messages")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 1 },
        run: { run_id: "run_chat", input: { source: "web_chat", session_id: "session_web" }, state: { status: "queued" } },
      });
    }
    if (path.startsWith("/api/runs/run_chat/events")) {
      return response({
        events: [
          {
            seq: 1,
            type: "assistant_delta",
            created_at: "2026-08-20T07:01:02Z",
            payload: { kind: "deepagent_message_delta", delta: "正在回答" },
          },
        ],
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  fireEvent.change(screen.getByPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行"), {
    target: { value: "你好" },
  });
  fireEvent.click(screen.getByRole("button", { name: /发送/ }));

  expect(await screen.findByText("你好")).toBeInTheDocument();
  expect(await screen.findByText("正在回答")).toBeInTheDocument();
});

test("chat page does not send when enter confirms ime composition", async () => {
  window.history.replaceState({}, "", "/chat");
  let messageCalls = 0;
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", content: CONFIG_YAML });
    }
    if (path.endsWith("/api/chat/session")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 0 },
        messages: [],
      });
    }
    if (path.startsWith("/api/chat/sessions?")) {
      return response({
        sessions: [{ session_id: "session_web", agent_id: "assistant", active: true, message_count: 0 }],
      });
    }
    if (path.endsWith("/api/chat/messages")) {
      messageCalls += 1;
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 1 },
        run: { run_id: "run_ime", input: { source: "web_chat", session_id: "session_web" }, state: { status: "queued" } },
      });
    }
    if (path.startsWith("/api/runs/run_ime/events")) {
      return response({ events: [] });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  const textarea = screen.getByPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行");
  fireEvent.change(textarea, { target: { value: "nihao" } });
  fireEvent.compositionStart(textarea);
  fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", keyCode: 229, isComposing: true });
  await flushReact();

  expect(messageCalls).toBe(0);

  fireEvent.compositionEnd(textarea);
  fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", keyCode: 13 });
  await flushReact();

  expect(messageCalls).toBe(1);
});

test("chat page renders assistant markdown and follows the latest message", async () => {
  window.history.replaceState({}, "", "/chat");
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get() {
      return 900;
    },
  });
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", content: CONFIG_YAML });
    }
    if (path.endsWith("/api/chat/session")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 0 },
        messages: [],
      });
    }
    if (path.endsWith("/api/chat/messages")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 1 },
        run: { run_id: "run_markdown", input: { source: "web_chat", session_id: "session_web" }, state: { status: "queued" } },
      });
    }
    if (path.startsWith("/api/runs/run_markdown/events")) {
      return response({
        events: [
          {
            seq: 1,
            type: "assistant_delta",
            created_at: "2026-08-20T07:01:02Z",
            payload: { kind: "deepagent_message_delta", delta: "**重点**\n- 第一条" },
          },
        ],
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  fireEvent.change(screen.getByPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行"), {
    target: { value: "给我 markdown" },
  });
  fireEvent.click(screen.getByRole("button", { name: /发送/ }));

  expect(await screen.findByText("重点")).toBeInTheDocument();
  expect(screen.getByText("重点").tagName).toBe("STRONG");
  expect(screen.getByText("第一条").tagName).toBe("LI");
  await waitFor(() => {
    expect(document.querySelector(".chat-messages").scrollTop).toBe(900);
  });
});

test("chat page renders assistant markdown tables", async () => {
  window.history.replaceState({}, "", "/chat");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", content: CONFIG_YAML });
    }
    if (path.endsWith("/api/chat/session")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 1 },
        messages: [
          {
            seq: 1,
            role: "assistant",
            content: "方案：\n\n| 顺序 | 动作 | 为什么 |\n| --- | --- | --- |\n| 1 | 保底线 | 避免击穿 |\n| 2 | 提收益 | 增加弹性 |",
            created_at: "2026-08-20T07:01:04Z",
          },
        ],
      });
    }
    if (path.startsWith("/api/chat/sessions?")) {
      return response({ sessions: [{ session_id: "session_web", agent_id: "assistant", active: true, message_count: 1 }] });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  expect(await screen.findByRole("table")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "顺序" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "动作" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "保底线" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "增加弹性" })).toBeInTheDocument();
});

test("chat page shows thinking events while running and folds them after completion", async () => {
  vi.useFakeTimers();
  window.history.replaceState({}, "", "/chat");
  let eventPolls = 0;
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", content: CONFIG_YAML });
    }
    if (path.endsWith("/api/chat/session")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 0 },
        messages: [],
      });
    }
    if (path.endsWith("/api/chat/messages")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 1 },
        run: { run_id: "run_thinking", input: { source: "web_chat", session_id: "session_web" }, state: { status: "queued" } },
      });
    }
    if (path.startsWith("/api/runs/run_thinking/events")) {
      eventPolls += 1;
      if (eventPolls === 1) {
        return response({
          events: [
            { seq: 1, type: "running", created_at: "2026-08-20T07:01:01Z", payload: { message: "DeepAgent started" } },
            {
              seq: 2,
              type: "agent_update",
              created_at: "2026-08-20T07:01:02Z",
              payload: { kind: "deepagent_graph_update", preview: "正在搜索资料" },
            },
          ],
        });
      }
      return response({
        events: [
          {
            seq: 3,
            type: "assistant_delta",
            created_at: "2026-08-20T07:01:03Z",
            payload: { kind: "deepagent_message_delta", delta: "最终正文" },
          },
          { seq: 4, type: "completed", created_at: "2026-08-20T07:01:04Z", payload: { message: "run completed" } },
        ],
      });
    }
    if (path.endsWith("/api/runs/run_thinking")) {
      return response({
        run_id: "run_thinking",
        agent_id: "assistant",
        input: { agent_id: "assistant", source: "web_chat", session_id: "session_web" },
        state: { status: "completed", seq: 4 },
        result: { status: "completed", content: "最终正文" },
      });
    }
    if (path.endsWith("/api/chat/sessions/session_web/messages")) {
      return response({
        messages: [
          { seq: 1, role: "user", content: "生成一段话", run_id: "run_thinking", created_at: "2026-08-20T07:01:00Z" },
          { seq: 2, role: "assistant", content: "最终正文", run_id: "run_thinking", created_at: "2026-08-20T07:01:04Z" },
        ],
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  fireEvent.change(screen.getByPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行"), {
    target: { value: "生成一段话" },
  });
  fireEvent.click(screen.getByRole("button", { name: /发送/ }));

  await flushReact();

  expect(screen.getByText("正在搜索资料")).toBeInTheDocument();
  expect(screen.getByText("思考过程").closest("details").open).toBe(true);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_000);
  });
  await flushReact();

  expect(screen.getByText("最终正文")).toBeInTheDocument();
  expect(screen.getByText("思考过程").closest("details").open).toBe(false);
});

test("chat page restores folded thinking from run partial after refresh", async () => {
  window.history.replaceState({}, "", "/chat");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", content: CONFIG_YAML });
    }
    if (path.endsWith("/api/chat/session")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 2 },
        messages: [
          { seq: 1, role: "user", content: "生成一段话", run_id: "run_restored", created_at: "2026-08-20T07:01:00Z" },
          { seq: 2, role: "assistant", content: "最终正文", run_id: "run_restored", created_at: "2026-08-20T07:01:04Z" },
        ],
      });
    }
    if (path.endsWith("/api/runs/run_restored")) {
      return response({
        run_id: "run_restored",
        input: { agent_id: "assistant", source: "web_chat", session_id: "session_web" },
        state: { status: "completed", seq: 4 },
        result: { status: "completed", content: "最终正文" },
        partial: {
          status: "completed",
          content: "最终正文",
          thinking: ["DeepAgent started", "正在搜索资料"],
          thinking_collapsed: true,
        },
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  expect(await screen.findByText("最终正文")).toBeInTheDocument();
  expect(await screen.findByText("正在搜索资料")).toBeInTheDocument();
  expect(screen.getByText("思考过程").closest("details").open).toBe(false);
});

test("chat page restores an active run after refresh and keeps polling", async () => {
  window.history.replaceState({}, "", "/chat");
  let eventPolls = 0;
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", content: CONFIG_YAML });
    }
    if (path.endsWith("/api/chat/session")) {
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 1, last_run_id: "run_active" },
        messages: [{ seq: 1, role: "user", content: "继续", run_id: "run_active", created_at: "2026-08-20T07:01:00Z" }],
        active_run: {
          run_id: "run_active",
          input: { agent_id: "assistant", source: "web_chat", session_id: "session_web" },
          state: { status: "running", seq: 1 },
          partial: {
            status: "streaming",
            content: "已生成一半",
            thinking: ["DeepAgent started"],
            thinking_collapsed: false,
          },
        },
      });
    }
    if (path.startsWith("/api/chat/sessions?")) {
      return response({ sessions: [{ session_id: "session_web", agent_id: "assistant", active: true, message_count: 1 }] });
    }
    if (path.startsWith("/api/runs/run_active/events")) {
      eventPolls += 1;
      if (eventPolls === 1) {
        return response({ events: [] });
      }
      return response({
        events: [
          { seq: 1, type: "running", created_at: "2026-08-20T07:01:01Z", payload: { message: "DeepAgent started" } },
          {
            seq: 2,
            type: "assistant_delta",
            created_at: "2026-08-20T07:01:02Z",
            payload: { kind: "deepagent_message_delta", delta: "最终正文" },
          },
          { seq: 3, type: "completed", created_at: "2026-08-20T07:01:03Z", payload: { message: "run completed" } },
        ],
      });
    }
    if (path.endsWith("/api/runs/run_active")) {
      return response({
        run_id: "run_active",
        input: { agent_id: "assistant", source: "web_chat", session_id: "session_web" },
        state: { status: "completed", seq: 3 },
        result: { status: "completed", content: "最终正文" },
      });
    }
    if (path.endsWith("/api/chat/sessions/session_web/messages")) {
      return response({
        messages: [
          { seq: 1, role: "user", content: "继续", run_id: "run_active", created_at: "2026-08-20T07:01:00Z" },
          { seq: 2, role: "assistant", content: "最终正文", run_id: "run_active", created_at: "2026-08-20T07:01:03Z" },
        ],
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  expect(await screen.findByText("已生成一半")).toBeInTheDocument();
  expect(screen.getByText("DeepAgent started")).toBeInTheDocument();
  expect(screen.getByText("思考过程").closest("details").open).toBe(true);

  expect(await screen.findByText("最终正文")).toBeInTheDocument();
  expect(screen.getByText("思考过程").closest("details").open).toBe(false);
  expect(screen.getAllByText("DeepAgent started")).toHaveLength(1);
});

test("chat page switches between related sessions", async () => {
  window.history.replaceState({}, "", "/chat");
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", content: CONFIG_YAML });
    }
    if (path.endsWith("/api/chat/session")) {
      return response({
        session: { session_id: "session_new", agent_id: "assistant", message_count: 0 },
        messages: [],
      });
    }
    if (path.startsWith("/api/chat/sessions?")) {
      return response({
        sessions: [
          {
            session_id: "session_new",
            agent_id: "assistant",
            active: true,
            message_count: 0,
            updated_at: "2026-08-20T08:00:00Z",
          },
          {
            session_id: "session_old",
            agent_id: "assistant",
            active: false,
            message_count: 2,
            updated_at: "2026-08-20T07:00:00Z",
          },
        ],
      });
    }
    if (path.endsWith("/api/chat/session/change") && options.method === "POST") {
      return response({
        session: { session_id: "session_old", agent_id: "assistant", active: true, message_count: 2 },
        messages: [
          { seq: 1, role: "user", content: "旧问题", run_id: "run_old", created_at: "2026-08-20T07:01:00Z" },
          { seq: 2, role: "assistant", content: "旧回答", run_id: "run_old", created_at: "2026-08-20T07:01:04Z" },
        ],
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  await waitFor(() => expect(screen.getByTitle("切换历史会话")).not.toBeDisabled());
  fireEvent.click(screen.getByTitle("切换历史会话"));
  fireEvent.click(screen.getByRole("button", { name: /历史 2/ }));

  expect(await screen.findByText("旧问题")).toBeInTheDocument();
  expect(await screen.findByText("旧回答")).toBeInTheDocument();
  const changeCall = global.fetch.mock.calls.find(([url, options]) => String(url).endsWith("/api/chat/session/change") && options.method === "POST");
  expect(JSON.parse(changeCall[1].body)).toEqual({ agent_id: "assistant", selector: "session_old" });
});

test("opens config.yaml as a native workspace text file", async () => {
  window.history.replaceState({}, "", "/workspace");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/workspace/list")) {
      return response({
        path: "",
        entries: [{ name: "config.yaml", path: "config.yaml", type: "file", size: 128, modified_at: 1797750000, deletable: false }],
      });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({
        path: "config.yaml",
        size: 128,
        editable: true,
        content: CONFIG_YAML,
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  fireEvent.click((await screen.findAllByRole("button", { name: /config.yaml/ }))[0]);

  const editor = await screen.findByDisplayValue((value) => value.includes("auth:") && value.includes("server:"));
  expect(editor).toHaveClass("workspace-editor");
  expect(screen.queryByText("认证与服务")).not.toBeInTheDocument();
});

test("saves system config from the dedicated config menu", async () => {
  window.history.replaceState({}, "", "/config");
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({
        path: "config.yaml",
        size: 128,
        editable: true,
        content: CONFIG_YAML,
      });
    }
    if (path.endsWith("/api/workspace/write")) {
      return response({
        ok: true,
        message: "config.yaml 已校验并保存",
        file: { path: "config.yaml", size: 128, editable: true, content: JSON.parse(options.body || "{}").content || "" },
      });
    }
    if (path.endsWith("/api/system/webdav-context/sync")) {
      return response({
        ok: true,
        message: "WebDAV 已同步：2 个文本，1 个图片资源",
        summary: { documents: 2, assets: 1, total: 3 },
      });
    }
    if (path.endsWith("/api/system/webdav-context/test")) {
      return response({
        ok: true,
        message: "WebDAV 连接成功",
        target_url: "https://dav.jianguoyun.com/dav/notebook/",
        status_code: 207,
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("认证与服务")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /基础配置/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: /Providers/ })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Agents/ })).toBeInTheDocument();
  expect(screen.getByLabelText("访问 Token")).toHaveValue("secret-token");
  expect(screen.getByLabelText("访问 Token")).toHaveAttribute("type", "text");
  expect(screen.getByText("浏览器抓取")).toBeInTheDocument();
  expect(screen.getByLabelText("代理")).toHaveValue("http://127.0.0.1:7890");
  expect(screen.getByText("坚果云 WebDAV")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /测试连接/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /立即同步/ })).toBeInTheDocument();
  expect(screen.queryByText("Provider 默认项")).not.toBeInTheDocument();
  expect(screen.queryByText("DeepAgent 运行选项")).not.toBeInTheDocument();
  expect(screen.queryByText("微信账号")).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("监听端口"), { target: { value: "9999" } });
  fireEvent.change(screen.getByLabelText("代理"), { target: { value: "socks5://127.0.0.1:7890" } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));

  await waitFor(() => {
    const writeCall = global.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/workspace/write"));
    expect(writeCall).toBeTruthy();
    expect(JSON.parse(writeCall[1].body).content).toContain("port: 9999");
    expect(JSON.parse(writeCall[1].body).content).toContain('proxy: "socks5://127.0.0.1:7890"');
  });

  fireEvent.click(screen.getByRole("button", { name: /立即同步/ }));

  await waitFor(() => {
    expect(screen.getByText("WebDAV 已同步：2 个文本，1 个图片资源")).toBeInTheDocument();
  });

  fireEvent.click(screen.getByRole("button", { name: /测试连接/ }));

  await waitFor(() => {
    expect(screen.getByText(/WebDAV 连接成功/)).toBeInTheDocument();
  });
});

test("keeps browser authorization on a dedicated page", async () => {
  window.history.replaceState({}, "", "/browser");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/system/browser-profiles")) {
      return response({
        agents: [{ id: "assistant", name: "默认助手" }],
        profiles: [
          {
            agent_id: "assistant",
            profile_path: "/workspace/browser_profiles/assistant",
            exists: true,
            locked: false,
          },
        ],
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("授权会话")).toBeInTheDocument();
  expect(screen.getAllByText("/workspace/browser_profiles/assistant").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: /启动授权/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^浏览器$/ })).toHaveClass("active");
});

test("system page no longer contains browser authorization", async () => {
  window.history.replaceState({}, "", "/system");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/system/logs/list")) {
      return response({ logs: [] });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect((await screen.findAllByText("运维")).length).toBeGreaterThan(0);
  expect(screen.getByText("生产更新、运行日志、工作目录入口")).toBeInTheDocument();
  expect(screen.queryByText("浏览器授权")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /启动授权/ })).not.toBeInTheDocument();
});

test("saves provider config from the provider menu", async () => {
  window.history.replaceState({}, "", "/providers");
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", size: 128, editable: true, content: CONFIG_YAML });
    }
    if (path.endsWith("/api/workspace/write")) {
      return response({
        ok: true,
        message: "config.yaml 已校验并保存",
        file: { path: "config.yaml", size: 128, editable: true, content: JSON.parse(options.body || "{}").content || "" },
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("Provider 默认项")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Providers/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByText("微信账号")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("模型名"), { target: { value: "gpt-4.1-mini" } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));

  await waitFor(() => {
    const writeCall = global.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/workspace/write"));
    expect(writeCall).toBeTruthy();
    expect(JSON.parse(writeCall[1].body).content).toContain('model: "gpt-4.1-mini"');
  });
});

test("keeps focus while editing provider id", async () => {
  window.history.replaceState({}, "", "/providers");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", size: 128, editable: true, content: CONFIG_YAML });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  const idInput = await screen.findByLabelText("ID");
  idInput.focus();
  fireEvent.change(idInput, { target: { value: "defaultx" } });
  expect(document.activeElement).toBe(idInput);
});

test("keeps focus while editing agent id", async () => {
  window.history.replaceState({}, "", "/agent-config");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", size: 128, editable: true, content: CONFIG_YAML });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  const idInput = await screen.findByLabelText("ID");
  idInput.focus();
  fireEvent.change(idInput, { target: { value: "assistantx" } });
  expect(document.activeElement).toBe(idInput);
});

test("loads provider config with yaml indentless sequences", async () => {
  window.history.replaceState({}, "", "/providers");
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", size: 128, editable: true, content: INDENTLESS_SEQUENCE_CONFIG_YAML });
    }
    if (path.endsWith("/api/workspace/write")) {
      return response({
        ok: true,
        message: "config.yaml 已校验并保存",
        file: { path: "config.yaml", size: 128, editable: true, content: JSON.parse(options.body || "{}").content || "" },
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect((await screen.findAllByDisplayValue("ds-pro")).length).toBeGreaterThanOrEqual(2);
  expect(screen.getByDisplayValue("deepseek-v4-pro")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Temperature"), { target: { value: "0.5" } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));

  await waitFor(() => {
    const writeCall = global.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/workspace/write"));
    expect(writeCall).toBeTruthy();
    const content = JSON.parse(writeCall[1].body).content;
    expect(content).toContain('default_model_id: "ds-pro"');
    expect(content).toContain('- id: "ds-pro"');
    expect(content).toContain("temperature: 0.5");
  });
});

test("deletes a provider and migrates model references", async () => {
  window.history.replaceState({}, "", "/providers");
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", size: 128, editable: true, content: TWO_PROVIDER_CONFIG_YAML });
    }
    if (path.endsWith("/api/workspace/write")) {
      return response({
        ok: true,
        message: "config.yaml 已校验并保存",
        file: { path: "config.yaml", size: 128, editable: true, content: JSON.parse(options.body || "{}").content || "" },
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByDisplayValue("primary-model")).toBeInTheDocument();
  fireEvent.click(screen.getAllByTitle("删除模型")[0]);
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));

  await waitFor(() => {
    const writeCall = global.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/workspace/write"));
    expect(writeCall).toBeTruthy();
    const content = JSON.parse(writeCall[1].body).content;
    expect(content).not.toContain('id: "primary"');
    expect(content).toContain('default_model_id: "backup"');
    expect(content).toContain('model_id: "backup"');
  });
});

test("saves deepagent options from the agent config menu", async () => {
  window.history.replaceState({}, "", "/agent-config");
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", size: 128, editable: true, content: CONFIG_YAML });
    }
    if (path.endsWith("/api/workspace/write")) {
      return response({
        ok: true,
        message: "config.yaml 已校验并保存",
        file: { path: "config.yaml", size: 128, editable: true, content: JSON.parse(options.body || "{}").content || "" },
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("DeepAgent 运行选项")).toBeInTheDocument();
  expect(screen.queryByText("微信账号")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Max Iterations"), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "配置工具" }));
  expect(screen.getByRole("dialog", { name: "Agent 工具授权" })).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Search Context"));
  fireEvent.click(screen.getByLabelText("Write Context"));
  fireEvent.click(screen.getByLabelText("Schedule"));
  fireEvent.click(screen.getByRole("button", { name: "完成" }));
  fireEvent.click(screen.getByLabelText("Agent 文件系统"));
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));

  await waitFor(() => {
    const writeCall = global.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/workspace/write"));
    expect(writeCall).toBeTruthy();
    const content = JSON.parse(writeCall[1].body).content;
    expect(content).toContain("max_iterations: 12");
    expect(content).toContain("todo_list: true");
    expect(content).toContain("use_longterm_memory: true");
    expect(content).toContain("filesystem:");
    expect(content).toContain("enabled: true");
    expect(content).toContain('root: "agent"');
    expect(content).toContain('mode: "read_write"');
    expect(content).toContain('- "search_context"');
    expect(content).toContain('- "write_context"');
    expect(content).toContain('- "schedule"');
  });
});

test("shows built-in schedules without the agent task form", async () => {
  window.history.replaceState({}, "", "/schedules");
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", size: 128, editable: true, content: CONFIG_YAML });
    }
    if (path.endsWith("/api/schedules")) {
      return response({
        schedules: [
          {
            summary: {
              id: "context_webdav_sync",
              name: "Context WebDAV 同步",
              enabled: true,
              status: "running",
              trigger: { kind: "interval", seconds: 600 },
            },
          },
          {
            summary: {
              id: "maintenance_cleanup",
              name: "维护清理",
              enabled: true,
              status: "completed",
              trigger: { kind: "interval", seconds: 86400 },
            },
          },
        ],
      });
    }
    if (path.endsWith("/api/schedules/context_webdav_sync")) {
      return response({
        definition: {
          id: "context_webdav_sync",
          name: "Context WebDAV 同步",
          type: "webdav_sync",
          enabled: true,
          built_in: true,
          trigger: { kind: "interval", seconds: 600 },
        },
        state: { status: "running", next_run_at: "2026-08-24T06:53:28Z", last_run_at: "2026-08-24T06:43:28Z" },
        events: [],
      });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("内置任务由系统配置驱动，只能在这里查看状态或立即执行。")).toBeInTheDocument();
  expect(screen.getByText("维护清理")).toBeInTheDocument();
  expect(screen.getByText("webdav_sync")).toBeInTheDocument();
  expect(screen.queryByLabelText("Prompt")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Agent")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /立即运行/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /保存/ })).not.toBeInTheDocument();
});

test("updates the selected agent for a wechat account", async () => {
  window.history.replaceState({}, "", "/wechat");
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/channels/wechat/accounts") && (!options.method || options.method === "GET")) {
      return response({
        accounts: [
          {
            id: "main",
            name: "主账号",
            default_agent_id: "assistant",
            auto_start: false,
            proxy: "",
            status: { login_state: "stopped", running: false, logs: [] },
          },
        ],
      });
    }
    if (path.endsWith("/api/channels/wechat/accounts/main")) {
      return response({ ok: true, account: { id: "main", default_agent_id: JSON.parse(options.body || "{}").default_agent_id } });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", size: 128, editable: true, content: CONFIG_YAML });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect((await screen.findAllByText("主账号")).length).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText("默认 Agent"), { target: { value: "" } });

  await waitFor(() => {
    const updateCall = global.fetch.mock.calls.find(([url, options]) => String(url).endsWith("/api/channels/wechat/accounts/main") && options.method === "PUT");
    expect(updateCall).toBeTruthy();
    expect(JSON.parse(updateCall[1].body).default_agent_id).toBe("");
  });
});

test("creates and deletes a wechat account", async () => {
  window.history.replaceState({}, "", "/wechat");
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  let accounts = [
    {
      id: "main",
      name: "主账号",
      default_agent_id: "assistant",
      auto_start: false,
      proxy: "",
      status: { login_state: "stopped", running: false, logs: [] },
    },
  ];
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/runs")) {
      return response({ runs: [] });
    }
    if (path.endsWith("/api/channels/wechat/accounts") && (!options.method || options.method === "GET")) {
      return response({ accounts });
    }
    if (path.endsWith("/api/channels/wechat/accounts") && options.method === "POST") {
      const body = JSON.parse(options.body || "{}");
      accounts = [...accounts, { ...body, status: { login_state: "stopped", running: false, logs: [] } }];
      return response({ ok: true, account: body });
    }
    if (path.endsWith("/api/channels/wechat/accounts/side") && options.method === "DELETE") {
      accounts = accounts.filter((account) => account.id !== "side");
      return response({ ok: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", size: 128, editable: true, content: CONFIG_YAML });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect((await screen.findAllByText("主账号")).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: /新增/ }));
  fireEvent.change(screen.getByLabelText("账号 ID"), { target: { value: "side" } });
  fireEvent.change(screen.getByLabelText("显示名称"), { target: { value: "副账号" } });
  fireEvent.change(screen.getAllByLabelText("默认 Agent")[0], { target: { value: "assistant" } });
  fireEvent.click(screen.getByRole("button", { name: /保存账号/ }));

  await waitFor(() => {
    const createCall = global.fetch.mock.calls.find(([url, options]) => String(url).endsWith("/api/channels/wechat/accounts") && options.method === "POST");
    expect(createCall).toBeTruthy();
    expect(JSON.parse(createCall[1].body)).toMatchObject({
      id: "side",
      name: "副账号",
      default_agent_id: "assistant",
      auto_start: false,
      proxy: "",
    });
  });
  expect((await screen.findAllByText("副账号")).length).toBeGreaterThan(0);

  fireEvent.click(screen.getByRole("button", { name: "删除账号" }));
  await waitFor(() => {
    expect(confirmSpy).toHaveBeenCalled();
    const deleteCall = global.fetch.mock.calls.find(([url, options]) => String(url).endsWith("/api/channels/wechat/accounts/side") && options.method === "DELETE");
    expect(deleteCall).toBeTruthy();
  });
});
