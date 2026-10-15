import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  "network:",
  '  proxy: "http://192.168.1.2:7893"',
  "browser:",
  '  proxy: ""',
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
  "        subagents: []",
  '        response_format: ""',
  '        context_schema: ""',
  "        checkpointer: true",
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
let clipboardDescriptor;

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
  clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
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
  if (clipboardDescriptor) {
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  } else {
    delete navigator.clipboard;
  }
});

test("renders the DeepAgent console shell", async () => {
  await act(async () => {
    await import("./main.jsx");
  });

  expect((await screen.findAllByText("DeepAgent")).length).toBeGreaterThan(1);
  expect((await screen.findAllByText("Runs")).length).toBeGreaterThan(1);
  expect(await screen.findByText("workspace/runs/index.json")).toBeInTheDocument();
  expect(await screen.findByText("配置")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Providers/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Agents/ })).not.toBeInTheDocument();
  expect(await screen.findByText("微信")).toBeInTheDocument();
});

test("opens and closes the compact navigation drawer", async () => {
  await act(async () => {
    await import("./main.jsx");
  });

  const menuButton = await screen.findByRole("button", { name: "打开导航" });
  const sidebar = document.querySelector(".sidebar");
  expect(sidebar).not.toHaveClass("mobile-open");

  fireEvent.click(menuButton);
  expect(menuButton).toHaveAttribute("aria-expanded", "true");
  expect(sidebar).toHaveClass("mobile-open");

  fireEvent.click(document.querySelector(".mobile-nav-backdrop"));
  expect(menuButton).toHaveAttribute("aria-expanded", "false");
  expect(sidebar).not.toHaveClass("mobile-open");
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

  changeInput(await screen.findByRole("textbox", { name: "输入消息，Enter 发送，Shift+Enter 换行" }), {
    target: { value: "你好" },
  });
  fireEvent.click(screen.getByRole("button", { name: /发送/ }));

  expect(await screen.findByText("你好")).toBeInTheDocument();
  expect(await screen.findByText("正在回答")).toBeInTheDocument();
});

test("chat page creates only one run when send is triggered twice before the request finishes", async () => {
  window.history.replaceState({}, "", "/chat");
  let messageCalls = 0;
  let resolveMessage;
  let requestPayload;
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) return response({ authenticated: true });
    if (path.endsWith("/api/workspace/read")) return response({ path: "config.yaml", content: CONFIG_YAML });
    if (path.endsWith("/api/chat/session")) {
      return response({ session: { session_id: "session_web", agent_id: "assistant", message_count: 0 }, messages: [] });
    }
    if (path.endsWith("/api/chat/messages")) {
      messageCalls += 1;
      requestPayload = JSON.parse(options.body);
      return new Promise((resolve) => {
        resolveMessage = () =>
          resolve(
            response({
              session: { session_id: "session_web", agent_id: "assistant", message_count: 1 },
              run: { run_id: "run_once", state: { status: "queued" } },
            }),
          );
      });
    }
    if (path.startsWith("/api/runs/run_once/events")) return response({ events: [] });
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  const textarea = await screen.findByRole("textbox", { name: "输入消息，Enter 发送，Shift+Enter 换行" });
  changeInput(textarea, { target: { value: "只发送一次" } });
  act(() => {
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", keyCode: 13 });
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", keyCode: 13 });
  });

  expect(messageCalls).toBe(1);
  expect(requestPayload.client_message_id).toBeTruthy();
  expect(screen.getAllByText("只发送一次")).toHaveLength(1);

  await act(async () => {
    resolveMessage();
  });
  await flushReact();
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

  const textarea = await screen.findByRole("textbox", { name: "输入消息，Enter 发送，Shift+Enter 换行" });
  changeInput(textarea, { target: { value: "nihao" } });
  fireEvent.compositionStart(textarea);
  fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", keyCode: 229, isComposing: true });
  await flushReact();

  expect(messageCalls).toBe(0);

  fireEvent.compositionEnd(textarea);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 550)); });
  fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", keyCode: 13 });
  await flushReact();

  expect(messageCalls).toBe(1);
});

test("chat page renders assistant markdown and follows the latest message", async () => {
  window.history.replaceState({}, "", "/chat");
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
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
            payload: { kind: "deepagent_message_delta", delta: "**重点**\n- 第一条\n\n---\n\n结尾" },
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

  changeInput(await screen.findByRole("textbox", { name: "输入消息，Enter 发送，Shift+Enter 换行" }), {
    target: { value: "给我 markdown" },
  });
  fireEvent.click(screen.getByRole("button", { name: /发送/ }));

  expect(await screen.findByText("重点")).toBeInTheDocument();
  expect(screen.getByText("重点").tagName).toBe("STRONG");
  expect(screen.getByText("第一条").tagName).toBe("LI");
  expect(screen.getByText("结尾").tagName).toBe("P");
  expect(document.querySelector(".markdown-message hr")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "复制助手消息" }));
  await waitFor(() => {
    expect(writeText).toHaveBeenCalledWith("**重点**\n- 第一条\n\n---\n\n结尾");
    expect(screen.getByRole("button", { name: "已复制助手消息" })).toBeInTheDocument();
  });
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
              payload: {
                kind: "deepagent_graph_update",
                nodes: ["model"],
                preview: "我先看一下文件",
                message_type: "ai",
                tool_calls: ["read_file"],
              },
            },
            {
              seq: 3,
              type: "agent_update",
              created_at: "2026-08-20T07:01:02Z",
              payload: {
                kind: "deepagent_graph_update",
                nodes: ["tools"],
                preview: "正在搜索资料",
                message_type: "tool",
              },
            },
            {
              seq: 4,
              type: "subagent_response",
              created_at: "2026-08-20T07:01:02Z",
              payload: { kind: "deepagent_subagent_response", agent: "researcher", content: "已完成资料核对" },
            },
          ],
        });
      }
      return response({
        events: [
          {
            seq: 5,
            type: "assistant_delta",
            created_at: "2026-08-20T07:01:03Z",
            payload: { kind: "deepagent_message_delta", delta: "最终正文" },
          },
          { seq: 6, type: "completed", created_at: "2026-08-20T07:01:04Z", payload: { message: "run completed" } },
        ],
      });
    }
    if (path.endsWith("/api/runs/run_thinking")) {
      return response({
        run_id: "run_thinking",
        agent_id: "assistant",
        input: { agent_id: "assistant", source: "web_chat", session_id: "session_web" },
        state: { status: "completed", seq: 6 },
        result: { status: "completed", content: "最终正文" },
      });
    }
    if (path.includes("/api/chat/sessions/session_web/messages")) {
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

  changeInput(screen.getByRole("textbox", { name: "输入消息，Enter 发送，Shift+Enter 换行" }), {
    target: { value: "生成一段话" },
  });
  fireEvent.click(screen.getByRole("button", { name: /发送/ }));

  await flushReact();

  expect(screen.getByText("调用工具：read_file")).toBeInTheDocument();
  expect(screen.getByText("正在搜索资料")).toBeInTheDocument();
  expect(screen.getByText("子 Agent researcher：已完成资料核对")).toBeInTheDocument();
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
        state: { status: eventPolls < 2 ? "running" : "completed", seq: eventPolls < 2 ? 1 : 3 },
        result: { status: "completed", content: "最终正文" },
      });
    }
    if (path.includes("/api/chat/sessions/session_web/messages")) {
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

test("chat page switches to a WeChat session owned by the selected agent", async () => {
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
            channel: "web",
            peer_type: "private",
            peer_id: "browser",
            active: true,
            selected: true,
            message_count: 0,
            updated_at: "2026-08-20T08:00:00Z",
          },
          {
            session_id: "session_old",
            agent_id: "assistant",
            channel: "wechat",
            channel_account_id: "main",
            peer_type: "private",
            peer_id: "wxid_user",
            active: true,
            selected: false,
            message_count: 2,
            updated_at: "2026-08-20T07:00:00Z",
          },
        ],
      });
    }
    if (path.endsWith("/api/chat/session/change") && options.method === "POST") {
      return response({
        session: {
          session_id: "session_old",
          agent_id: "assistant",
          channel: "wechat",
          channel_account_id: "main",
          peer_type: "private",
          peer_id: "wxid_user",
          active: true,
          selected: true,
          message_count: 2,
        },
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

  // Sessions live in the left list now; clicking an entry switches the chat.
  fireEvent.click(await screen.findByRole("button", { name: /微信.*wxid_user/ }));

  expect(await screen.findByText("旧问题")).toBeInTheDocument();
  expect(await screen.findByText("旧回答")).toBeInTheDocument();
  const changeCall = global.fetch.mock.calls.find(([url, options]) => String(url).endsWith("/api/chat/session/change") && options.method === "POST");
  expect(JSON.parse(changeCall[1].body)).toEqual({ agent_id: "assistant", selector: "session_old" });
});

test("chat page deletes a session from the switcher menu", async () => {
  window.history.replaceState({}, "", "/chat");
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  let deleted = false;
  const webSession = {
    session_id: "session_new",
    agent_id: "assistant",
    channel: "web",
    peer_type: "private",
    peer_id: "browser",
    active: true,
    selected: true,
    message_count: 3,
    title: "当前会话",
    updated_at: "2026-08-20T08:00:00Z",
  };
  const wechatSession = {
    session_id: "session_old",
    agent_id: "assistant",
    channel: "wechat",
    channel_account_id: "main",
    peer_type: "private",
    peer_id: "wxid_user",
    active: true,
    selected: false,
    message_count: 2,
    updated_at: "2026-08-20T07:00:00Z",
  };
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) {
      return response({ authenticated: true });
    }
    if (path.endsWith("/api/workspace/read")) {
      return response({ path: "config.yaml", content: CONFIG_YAML });
    }
    if (path.endsWith("/api/chat/session")) {
      return response({ session: webSession, messages: [] });
    }
    if (path.startsWith("/api/chat/sessions/session_old") && options.method === "DELETE") {
      deleted = true;
      return response({ deleted: "session_old", sessions: [webSession] });
    }
    if (path.startsWith("/api/chat/sessions?")) {
      return response({ sessions: deleted ? [webSession] : [webSession, wechatSession] });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  fireEvent.click(await screen.findByRole("button", { name: "删除会话 session_old" }));
  await flushReact();

  expect(confirmSpy).toHaveBeenCalled();
  const deleteCall = global.fetch.mock.calls.find(
    ([url, options]) => String(url).startsWith("/api/chat/sessions/session_old?") && options.method === "DELETE",
  );
  expect(deleteCall).toBeTruthy();
  expect(deleteCall[0]).toContain("agent_id=assistant");

  await flushReact();
  expect(screen.queryByRole("button", { name: "删除会话 session_old" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "删除会话 session_new" })).toBeInTheDocument();
});

test("chat session list hides untouched empty sessions", async () => {
  window.history.replaceState({}, "", "/chat");
  const emptySession = {
    session_id: "session_empty",
    agent_id: "assistant",
    channel: "web",
    peer_type: "private",
    peer_id: "browser",
    active: true,
    selected: true,
    message_count: 0,
    updated_at: "2026-08-20T09:00:00Z",
  };
  const usedSession = {
    session_id: "session_used",
    agent_id: "assistant",
    channel: "wechat",
    channel_account_id: "main",
    peer_type: "private",
    peer_id: "wxid_user",
    active: true,
    selected: false,
    message_count: 12,
    title: "论文精读",
    updated_at: "2026-08-20T08:00:00Z",
  };
  global.fetch = vi.fn(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) return response({ authenticated: true });
    if (path.endsWith("/api/workspace/read")) return response({ path: "config.yaml", content: CONFIG_YAML });
    if (path.endsWith("/api/chat/session")) return response({ session: emptySession, messages: [] });
    if (path.startsWith("/api/chat/sessions?")) return response({ sessions: [emptySession, usedSession] });
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  // Only the session with content is listed, and the counter matches it.
  expect(await screen.findByText("论文精读")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "删除会话 session_empty" })).not.toBeInTheDocument();
  expect(screen.queryByText("还没有会话，直接发消息就会新建一条；也可以点右上角 + 再开一个。")).not.toBeInTheDocument();
  expect(screen.getByText("新对话 · 未开始")).toBeInTheDocument();
});

test("chat session list shows titles and renames a session", async () => {
  window.history.replaceState({}, "", "/chat");
  const webSession = {
    session_id: "session_new",
    agent_id: "assistant",
    channel: "web",
    peer_type: "private",
    peer_id: "browser",
    active: true,
    selected: true,
    message_count: 4,
    title: "论文精读",
    title_source: "auto",
    updated_at: "2026-08-20T08:00:00Z",
  };
  let renamed = false;
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) return response({ authenticated: true });
    if (path.endsWith("/api/workspace/read")) return response({ path: "config.yaml", content: CONFIG_YAML });
    if (path.endsWith("/api/chat/session")) return response({ session: webSession, messages: [] });
    if (path.includes("/api/chat/sessions/session_new/rename") && options.method === "POST") {
      renamed = true;
      return response({
        session: { ...webSession, title: "深度阅读计划", title_source: "manual" },
        sessions: [{ ...webSession, title: "深度阅读计划", title_source: "manual" }],
      });
    }
    if (path.startsWith("/api/chat/sessions?")) {
      return response({ sessions: [{ ...webSession, title: renamed ? "深度阅读计划" : "论文精读" }] });
    }
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  // The list shows the session title instead of the channel label.
  expect(await screen.findByText("论文精读")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重命名会话 session_new" }));
  fireEvent.change(screen.getByLabelText("会话标题"), { target: { value: "深度阅读计划" } });
  fireEvent.submit(screen.getByRole("form", { name: "重命名会话" }));
  await flushReact();

  const renameCall = global.fetch.mock.calls.find(
    ([url, options]) => String(url).includes("/api/chat/sessions/session_new/rename") && options.method === "POST",
  );
  expect(renameCall).toBeTruthy();
  expect(JSON.parse(renameCall[1].body)).toEqual({ agent_id: "assistant", title: "深度阅读计划" });
  expect(await screen.findByText("深度阅读计划")).toBeInTheDocument();
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

  // The convention map must track the current workspace contract: retired tools
  // and retired directories must not survive here after backend removals.
  const conventionMap = screen.getByText("约定目录").closest(".panel");
  expect(within(conventionMap).getByText("workspace/sessions/active.json")).toBeInTheDocument();
  expect(within(conventionMap).queryByText(/chat_groups/)).not.toBeInTheDocument();
  expect(within(conventionMap).queryByText(/search_context/)).not.toBeInTheDocument();
  expect(within(conventionMap).queryByText(/write_context/)).not.toBeInTheDocument();
  expect(within(conventionMap).queryByText(/\/notes\//)).not.toBeInTheDocument();
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
  expect(screen.getByText("网络代理")).toBeInTheDocument();
  expect(screen.getByLabelText("全局出口代理")).toHaveValue("http://192.168.1.2:7893");
  expect(screen.getByText("浏览器抓取")).toBeInTheDocument();
  expect(screen.getByLabelText("代理覆盖")).toHaveValue("");
  expect(screen.getByText("坚果云 WebDAV")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /测试连接/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /立即同步/ })).toBeInTheDocument();
  expect(screen.queryByText("Provider 默认项")).not.toBeInTheDocument();
  expect(screen.queryByText("DeepAgent 运行选项")).not.toBeInTheDocument();
  expect(screen.queryByText("微信账号")).not.toBeInTheDocument();

  changeInput(screen.getByLabelText("监听端口"), { target: { value: "9999" } });
  changeInput(screen.getByLabelText("代理覆盖"), { target: { value: "direct" } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));

  await waitFor(() => {
    const writeCall = global.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/workspace/write"));
    expect(writeCall).toBeTruthy();
    expect(JSON.parse(writeCall[1].body).content).toContain("port: 9999");
    expect(JSON.parse(writeCall[1].body).content).toContain('proxy: "direct"');
    expect(JSON.parse(writeCall[1].body).content).toContain('proxy: "http://192.168.1.2:7893"');
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
            profile_path: "/workspace/agents/assistant/workspace/browser",
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
  expect(screen.getAllByText("/workspace/agents/assistant/workspace/browser").length).toBeGreaterThan(0);
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
  changeInput(screen.getByLabelText("模型名"), { target: { value: "gpt-4.1-mini" } });
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
  changeInput(idInput, { target: { value: "defaultx" } });
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

  fireEvent.click(await screen.findByRole("button", { name: /配置 Agent/ }));
  const idInput = await screen.findByLabelText("ID");
  idInput.focus();
  changeInput(idInput, { target: { value: "assistantx" } });
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
  changeInput(screen.getByLabelText("Temperature"), { target: { value: "0.5" } });
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

  expect(await screen.findByRole("button", { name: /配置 Agent/ })).toBeInTheDocument();
  expect(screen.queryByLabelText("最大执行步数")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /配置 Agent/ }));
  expect(screen.getByRole("dialog", { name: "配置 Agent" })).toBeInTheDocument();
  expect(screen.queryByText("微信账号")).not.toBeInTheDocument();
  changeInput(screen.getByLabelText("最大执行步数"), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "配置工具" }));
  expect(screen.queryByLabelText("Search Context")).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Schedule"));
  expect(screen.queryByLabelText("Checkpointer")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Runtime Name")).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("启用 WebDAV"));
  fireEvent.click(screen.getByRole("button", { name: "添加目录" }));
  expect(screen.getByLabelText("访问权限")).toHaveValue("write");
  changeInput(screen.getByLabelText("映射目录（相对于全局同步目录）"), { target: { value: "/项目资料" } });
  changeInput(screen.getByLabelText("目录说明"), { target: { value: "项目共享文档" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Agent" }));

  await waitFor(() => {
    const writeCall = global.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/workspace/write"));
    expect(writeCall).toBeTruthy();
    const content = JSON.parse(writeCall[1].body).content;
    expect(content).toContain("max_iterations: 12");
    expect(content).toContain("todo_list: true");
    expect(content).not.toContain("use_longterm_memory:");
    expect(content).not.toContain("filesystem:");
    expect(content).toContain("enabled: true");
    expect(content).toContain('path: "/项目资料"');
    expect(content).toContain('permission: "write"');
    expect(content).toContain("approval_required: true");
    expect(content).toContain('description: "项目共享文档"');
    expect(content).not.toContain("webdav_permissions:");
    expect(content).not.toContain('- "search_context"');
    expect(content).not.toContain('- "write_context"');
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
  changeInput(screen.getByLabelText("默认 Agent"), { target: { value: "" } });

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
  changeInput(screen.getByLabelText("账号 ID"), { target: { value: "side" } });
  changeInput(screen.getByLabelText("显示名称"), { target: { value: "副账号" } });
  changeInput(screen.getAllByLabelText("默认 Agent")[0], { target: { value: "assistant" } });
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

test("runs page approves a paused DeepAgent run and resumes it", async () => {
  window.history.replaceState({}, "", "/runs");
  let resolveResume;
  let resumed = false;
  const waitingRun = {
    run_id: "run_waiting",
    input: { agent_id: "assistant", source: "web_chat", created_at: "2026-09-08T08:00:00Z" },
    state: { status: "waiting_approval", seq: 3 },
    approval: {
      status: "pending",
      request: {
        interrupts: [
          {
            interrupt_id: "interrupt-1",
            actions: [
              {
                name: "write_file",
                description: "写入知识文件",
                args: { path: "/webdav/result.md" },
                allowed_decisions: ["approve", "reject"],
              },
            ],
          },
        ],
      },
    },
  };
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) return response({ authenticated: true });
    if (path.endsWith("/api/runs") && (!options.method || options.method === "GET")) {
      return response({
        runs: [{ run_id: "run_waiting", agent_id: "assistant", status: resumed ? "queued" : "waiting_approval", updated_at: "2026-09-08T08:00:00Z" }],
      });
    }
    if (path.endsWith("/api/runs/run_waiting/events")) return response({ events: [] });
    if (path.endsWith("/api/runs/run_waiting/resume")) {
      return new Promise((resolve) => { resolveResume = () => {
        resumed = true;
        resolve(response({
          ...waitingRun,
          state: { status: "queued", seq: 5 },
          approval: { status: "resume_queued", history: [{ resolution: { decision: "approve" } }] },
        }));
      }; });
    }
    if (path.endsWith("/api/runs/run_waiting")) return response(resumed ? {
      ...waitingRun,
      state: { status: "queued", seq: 5 },
      approval: { status: "resume_queued", history: [{ resolution: { decision: "approve" } }] },
    } : waitingRun);
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("等待操作审批")).toBeInTheDocument();
  expect(screen.getByText("write_file")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "仅批准本次" }));
  expect(await screen.findByRole("button", { name: "批准中…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "拒绝并继续" })).toBeDisabled();

  await act(async () => resolveResume());

  await waitFor(() => {
    const resumeCall = global.fetch.mock.calls.find(
      ([url, options]) => String(url).endsWith("/api/runs/run_waiting/resume") && options.method === "POST",
    );
    expect(resumeCall).toBeTruthy();
    const payload = JSON.parse(resumeCall[1].body);
    expect(payload.decision).toBe("approve");
    expect(payload.message).toBe("");
    expect(screen.queryByText("等待操作审批")).not.toBeInTheDocument();
    expect(screen.getByText("已批准，Agent 正在恢复运行")).toBeInTheDocument();
    expect(screen.getAllByText("排队中").length).toBeGreaterThan(0);
  });
});

test("usage summary filters agents and preserves unknown historical costs", async () => {
  window.history.replaceState({}, "", "/runs");
  const usage = { input_tokens: 1000, output_tokens: 200, cached_input_tokens: 0, model_calls: 2, reported_calls: 1, unknown_calls: 1, unpriced_calls: 1, estimated_costs: { USD: 0.012 }, execution_seconds: 3, models: ["test"], interrupted_segments: 0 };
  const runs = [
    { run_id: "new", agent_id: "assistant", status: "completed", created_at: new Date().toISOString(), usage },
    { run_id: "old", agent_id: "legacy", status: "completed", created_at: "2020-01-01T00:00:00Z" },
  ];
  global.fetch = vi.fn(async (url) => {
    if (String(url).endsWith("/api/auth/me")) return response({ authenticated: true });
    if (String(url).endsWith("/api/runs")) return response({ runs });
    if (String(url).endsWith("/events")) return response({ events: [] });
    return response(runs[0]);
  });
  await act(async () => { await import("./main.jsx"); });
  const filter = await screen.findByLabelText("消耗统计 Agent");
  const summary = document.querySelector(".usage-summary");
  expect(summary).toHaveTextContent("USD 0.012000");
  expect(summary).toHaveTextContent("1 / 2");
  changeInput(filter, { target: { value: "legacy" } });
  expect(summary).toHaveTextContent("未记录");
  expect(summary).toHaveTextContent("未估算");
  expect(summary).not.toHaveTextContent("USD 0.000000");
  changeInput(filter, { target: { value: "" } });
  changeInput(screen.getByLabelText("消耗统计时间"), { target: { value: "1" } });
  expect(summary).toHaveTextContent("1 / 1");
});

test("chat retries unconfirmed send with original identity and handles cancelled snapshot", async () => {
  window.history.replaceState({}, "", "/chat");
  const bodies = [];
  global.fetch = vi.fn(async (url, options) => {
    if (url === "/api/auth/me") return response({ authenticated: true });
    if (url === "/api/workspace/read") return response({ content: CONFIG_YAML });
    if (url === "/api/chat/session") return response({ session: { session_id: "s", agent_id: "assistant" }, messages: [] });
    if (url === "/api/chat/messages") {
      bodies.push(JSON.parse(options.body));
      if (bodies.length === 1) throw new Error("响应丢失");
      return response({ session: { session_id: "s", agent_id: "assistant" }, run: { run_id: "r" } });
    }
    if (String(url).includes("/events?")) return response({ events: [] });
    if (url === "/api/runs/r") return response({ state: { status: "cancelled" }, result: {} });
    return response({ messages: [], sessions: [] });
  });
  await act(async () => { await import("./main.jsx"); });
  await flushReact();
  const input = await screen.findByRole("textbox", { name: "输入消息，Enter 发送，Shift+Enter 换行" });
  changeInput(input, { target: { value: "需要保留的原文" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  fireEvent.click(await screen.findByRole("button", { name: "重试确认发送" }));
  await waitFor(() => expect(screen.getByText("已停止")).toBeInTheDocument());
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toEqual(bodies[0]);
  changeInput(input, { target: { value: "下一条" } });
  expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
});

test("slow chat polling is serial and repeated event IDs do not duplicate output", async () => {
  window.history.replaceState({}, "", "/chat");
  let release;
  let calls = 0;
  global.fetch = vi.fn(async (url) => {
    if (url === "/api/auth/me") return response({ authenticated: true });
    if (url === "/api/workspace/read") return response({ content: CONFIG_YAML });
    if (url === "/api/chat/session") return response({ session: { session_id: "s", agent_id: "assistant" }, messages: [], active_run: { run_id: "slow_serial", state: { status: "running", seq: 0 } } });
    if (String(url).startsWith("/api/runs/slow_serial/events?")) {
      calls++;
      if (calls === 1) await new Promise((resolve) => { release = resolve; });
      return response({ events: [{ seq: 1, type: "assistant_delta", payload: { delta: "唯一正文" } }] });
    }
    return response({ sessions: [] });
  });
  await act(async () => { await import("./main.jsx"); });
  await flushReact();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1150)); });
  expect(calls).toBe(1);
  await act(async () => release());
  expect(await screen.findByText("唯一正文")).toBeInTheDocument();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1150)); });
  expect(calls).toBe(2);
  expect(screen.getByText("唯一正文")).toBeInTheDocument();
});

test("chat quote uses saved sequence, restores snapshot and leaves copy body separate", async () => {
  window.history.replaceState({}, "", "/chat");
  let request;
  global.fetch = vi.fn(async (url, options) => {
    if (url === "/api/auth/me") return response({ authenticated: true });
    if (url === "/api/workspace/read") return response({ content: CONFIG_YAML });
    if (url === "/api/chat/session") return response({ session: { session_id: "quoted", agent_id: "assistant" }, messages: [{ seq: 7, role: "assistant", content: "原始消息" }, { seq: 8, role: "user", content: "以前的追问", metadata: { reply: { speaker: "助手", content: "已保存的引用" } } }] });
    if (url === "/api/chat/messages") { request = JSON.parse(options.body); return response({ session: { session_id: "quoted", agent_id: "assistant" }, run: { run_id: "quote_run" } }); }
    return response({ sessions: [], events: [] });
  });
  await act(async () => { await import("./main.jsx"); });
  await flushReact();
  expect(screen.getByText("已保存的引用")).toBeInTheDocument();
  const input = await screen.findByRole("textbox", { name: "输入消息，Enter 发送，Shift+Enter 换行" });
  fireEvent.click(screen.getAllByRole("button", { name: "引用消息" })[0]);
  await waitFor(() => expect(input).toHaveFocus());
  expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  changeInput(input, { target: { value: "现在的追问" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(request?.reply_to_seq).toBe(7));
  expect(request.content).toBe("现在的追问");
  await waitFor(() => expect(screen.queryByRole("button", { name: "取消引用" })).not.toBeInTheDocument());
});

test.each(["failed", "cancelled"])("chat reload restores %s runs without saved assistant messages in order", async (status) => {
  window.history.replaceState({}, "", "/chat");
  global.fetch = vi.fn(async (url) => {
    if (url === "/api/auth/me") return response({ authenticated: true });
    if (url === "/api/workspace/read") return response({ content: CONFIG_YAML });
    if (url === "/api/chat/session") return response({ session: { session_id: "s", agent_id: "assistant" }, messages: [
      { seq: 1, role: "user", content: "失败前的问题", run_id: "broken" },
      { seq: 2, role: "user", content: "之后的问题" },
    ] });
    if (url === "/api/runs/broken") return response({ run_id: "broken", state: { status }, result: { error: { message: "持久化的错误原因" } } });
    return response({ messages: [], sessions: [] });
  });
  await act(async () => { await import("./main.jsx"); });
  const error = await screen.findByText("持久化的错误原因");
  expect(screen.getByText("失败前的问题").compareDocumentPosition(error) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(error.compareDocumentPosition(screen.getByText("之后的问题")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(global.fetch.mock.calls.some(([url]) => String(url).includes("/api/runs/broken/events?"))).toBe(false);
});

function changeInput(node, event) {
  if (node.editor) {
    act(() => node.editor.commands.setContent(event.target.value, { contentType: "markdown" }));
  } else {
    fireEvent.change(node, event);
  }
}

test("chat page attaches an image, sends it as an attachment, and renders the thumbnail", async () => {
  window.history.replaceState({}, "", "/chat");
  const sent = [];
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) return response({ authenticated: true });
    if (path.endsWith("/api/workspace/read")) return response({ path: "config.yaml", content: CONFIG_YAML });
    if (path.endsWith("/api/chat/session")) {
      return response({ session: { session_id: "session_web", agent_id: "assistant", message_count: 0 }, messages: [] });
    }
    if (path.endsWith("/api/chat/messages")) {
      sent.push(JSON.parse(options.body));
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 1 },
        run: { run_id: "run_image", state: { status: "queued" } },
      });
    }
    if (path.startsWith("/api/runs/run_image/events")) return response({ events: [] });
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  const fileInput = document.querySelector(".chat-attachment-input");
  expect(fileInput).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "添加内容" }));
  expect(screen.getByRole("menuitem", { name: /图片/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("menuitem", { name: /图片/ }));
  expect(screen.queryByRole("menuitem", { name: /图片/ })).toBeNull();
  const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "shot.png", { type: "image/png" });
  await act(async () => {
    fireEvent.change(fileInput, { target: { files: [file] } });
  });
  await waitFor(() => expect(screen.getByAltText("shot.png")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0].content).toBe("");
  expect(sent[0].attachments).toHaveLength(1);
  expect(sent[0].attachments[0].type).toBe("image");
  expect(sent[0].attachments[0].mime).toBe("image/png");
  expect(sent[0].attachments[0].data_url.startsWith("data:image/png;base64,")).toBe(true);
  await waitFor(() => expect(document.querySelectorAll(".chat-message-images img")).toHaveLength(1));
});

test("chat page accepts a pasted screenshot as a pending image attachment", async () => {
  window.history.replaceState({}, "", "/chat");
  const sent = [];
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) return response({ authenticated: true });
    if (path.endsWith("/api/workspace/read")) return response({ path: "config.yaml", content: CONFIG_YAML });
    if (path.endsWith("/api/chat/session")) {
      return response({ session: { session_id: "session_web", agent_id: "assistant", message_count: 0 }, messages: [] });
    }
    if (path.endsWith("/api/chat/messages")) {
      sent.push(JSON.parse(options.body));
      return response({
        session: { session_id: "session_web", agent_id: "assistant", message_count: 1 },
        run: { run_id: "run_paste", state: { status: "queued" } },
      });
    }
    if (path.startsWith("/api/runs/run_paste/events")) return response({ events: [] });
    return response({});
  });

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  const region = document.querySelector(".chat-composer-region");
  const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "clipboard.png", { type: "image/png" });
  await act(async () => {
    fireEvent.paste(region, { clipboardData: { files: [file], getData: () => "" } });
  });
  await waitFor(() => expect(screen.getByAltText("clipboard.png")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0].content).toBe("");
  expect(sent[0].attachments).toHaveLength(1);
  expect(sent[0].attachments[0].filename).toBe("clipboard.png");
});

const MARKET_FRAME = (timeframe, fast, overrides = {}) => ({
  timeframe,
  fast,
  last_bar_time: 1700000000000,
  last_close: fast ? 67842.15 : 412.6,
  change_pct: fast ? 0.42 : -0.31,
  closed: true,
  seconds_to_close: 0,
  trend: fast ? "up" : "flat",
  momentum: fast ? "强" : "中",
  volatility: fast ? "低" : "高",
  volume_level: fast ? "放量" : "正常",
  signals: [],
  closes: Array.from({ length: 20 }, (_, index) => 100 + index),
  indicators: {
    ema20: 1,
    ema60: 1,
    ema200: 1,
    rsi14: fast ? 71.4 : 55.8,
    macd: 1,
    macd_signal: 1,
    macd_hist: 0.5,
    atr14: 12.3,
    atr_ratio: 1,
    bollinger_upper: 1,
    bollinger_middle: 1,
    bollinger_lower: 1,
    volume_ratio: fast ? 1.8 : 1.1,
    swing_high: 1,
    swing_low: 1,
  },
  ...overrides,
});

function marketSnapshotPayload() {
  return {
    snapshot: {
      symbol: "BTC/USDT",
      alias: "比特币",
      source: "binance",
      last_price: 67842.15,
      change_pct_24h: 1.24,
      high_24h: 68410,
      low_24h: 66215.5,
      volume_24h: 18402,
      updated_at: "2026-10-15T09:31:04",
      data_age_seconds: 1,
      stale: false,
      timeframes: [
        MARKET_FRAME("1m", true, { signals: ["1m 放量突破 67,800"] }),
        MARKET_FRAME("5m", true),
        MARKET_FRAME("1h", false, { signals: ["1h 均线金叉"] }),
        MARKET_FRAME("4h", false),
      ],
    },
    events: { unread: 0, latest_seq: 0, last_read_seq: 0 },
  };
}

function marketFetch({ enabled = true, snapshot = marketSnapshotPayload(), events = [], onAnalyze = null } = {}) {
  return vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/api/auth/me")) return response({ authenticated: true });
    if (path.includes("/api/market/overview")) {
      return response({
        enabled,
        sources: ["binance", "gate"],
        feed: [],
        commentary_agent: "assistant",
        commentary_enabled: true,
        symbols: enabled
          ? [{ symbol: "BTC/USDT", alias: "比特币", last_price: 67842.15, change_pct_24h: 1.24, timeframes: [] }]
          : [],
        events: { unread: 2, latest_seq: 4, last_read_seq: 2 },
      });
    }
    if (path.includes("/api/market/snapshot")) return response(snapshot);
    if (path.includes("/api/market/events")) {
      return response({ events, summary: { unread: 0, latest_seq: 0, last_read_seq: 0 } });
    }
    if (path.includes("/api/market/commentary")) return response({ commentary: [], active_run: null, session_id: "" });
    if (path.includes("/api/market/analyze")) {
      if (onAnalyze) onAnalyze(options);
      return response({ run_id: "run_market_1", session_id: "session_1", symbol: "BTC/USDT" });
    }
    return response({});
  });
}

const MARKET_EVENT = {
  seq: 7,
  at: "2026-10-15T19:52:36",
  symbol: "ETH/USDT",
  timeframe: "1d",
  rule_id: "breakdown_low",
  severity: "high",
  title: "1d 跌破 2,403.69",
  detail: "收盘 2,366.14 低于前 20 根低点",
  price: 2366.14,
};

test("market page explains how to enable the fast layer when it is switched off", async () => {
  window.history.replaceState({}, "", "/market");
  global.fetch = marketFetch({ enabled: false });

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("market.enabled 当前为 false")).toBeInTheDocument();
  expect(await screen.findByText(/行情工作台被显式关掉了/)).toBeInTheDocument();
  expect(screen.getByText(/代理不用在这里配/)).toBeInTheDocument();
  expect(screen.queryByText("多周期信号矩阵")).not.toBeInTheDocument();
});

test("market matrix splits fast and slow timeframes and shows their signals", async () => {
  window.history.replaceState({}, "", "/market");
  global.fetch = marketFetch();

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("多周期信号矩阵")).toBeInTheDocument();
  expect(await screen.findByText("慢层 · 1h 及以上")).toBeInTheDocument();
  expect(await screen.findByText("1m 放量突破 67,800")).toBeInTheDocument();
  expect(await screen.findByText("1h 均线金叉")).toBeInTheDocument();

  const rows = document.querySelectorAll(".market-table tbody tr");
  const fastRows = [...rows].filter((row) => row.classList.contains("fast"));
  const slowRows = [...rows].filter((row) => row.classList.contains("slow"));
  expect(fastRows).toHaveLength(2);
  expect(slowRows).toHaveLength(2);
  expect(screen.getByText("快层 秒级")).toBeInTheDocument();
  expect(screen.getByText("慢层 1h+")).toBeInTheDocument();
});

test("market polling keeps the matrix DOM when the snapshot has not changed", async () => {
  vi.useFakeTimers();
  window.history.replaceState({}, "", "/market");
  global.fetch = marketFetch();

  await act(async () => {
    await import("./main.jsx");
  });
  await flushReact();

  const divider = screen.getByText("慢层 · 1h 及以上");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
  });

  // 同样的快照不应该重建节点，否则每 5 秒矩阵都会闪一下。
  expect(screen.getByText("慢层 · 1h 及以上")).toBe(divider);
});

test("market analyze hands the symbol to the agent and reports it back", async () => {
  window.history.replaceState({}, "", "/market");
  const analyzeBodies = [];
  global.fetch = marketFetch({
    onAnalyze: (options) => analyzeBodies.push(JSON.parse(options.body)),
  });

  await act(async () => {
    await import("./main.jsx");
  });

  const button = await screen.findByRole("button", { name: /让 Agent 分析一次/ });
  await act(async () => {
    fireEvent.click(button);
  });

  expect(analyzeBodies).toEqual([{ symbol: "BTC/USDT" }]);
  expect(await screen.findByText(/已派给 Agent/)).toBeInTheDocument();
});

test("market watchlist posts a new symbol and rejects a malformed one", async () => {
  window.history.replaceState({}, "", "/market");
  const posted = [];
  const base = marketFetch();
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.includes("/api/market/symbols") && (options.method || "GET") === "POST") {
      const body = JSON.parse(options.body);
      posted.push(body);
      if (body.symbol !== "BTC/USDT" && body.symbol !== "ETH/USDT") {
        return { ok: false, status: 400, json: async () => ({ detail: "symbol must look like BTC/USDT" }) };
      }
      return response({ symbol: body.symbol, alias: "" });
    }
    return base(url, options);
  });

  await act(async () => {
    await import("./main.jsx");
  });

  const input = await screen.findByPlaceholderText("BTC/USDT");
  fireEvent.change(input, { target: { value: "ETH/USDT" } });
  await act(async () => {
    fireEvent.keyDown(input, { key: "Enter" });
  });
  expect(posted).toEqual([{ symbol: "ETH/USDT" }]);
  expect(await screen.findByText("已添加 ETH/USDT")).toBeInTheDocument();

  fireEvent.change(input, { target: { value: "BTCUSDT" } });
  await act(async () => {
    fireEvent.keyDown(input, { key: "Enter" });
  });
  expect(await screen.findByText("symbol must look like BTC/USDT")).toBeInTheDocument();
});

test("market event feed is cross-symbol and matches the unread badge", async () => {
  window.history.replaceState({}, "", "/market");
  // 当前选中的是 BTC，但事件来自 ETH：告警流不该被选中的标的过滤掉。
  global.fetch = marketFetch({ events: [MARKET_EVENT] });

  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("1d 跌破 2,403.69")).toBeInTheDocument();
  expect(screen.getByText("ETH/USDT")).toBeInTheDocument();
  expect(screen.getByText(/全部标的 · 最近 1 条/)).toBeInTheDocument();
  expect(document.querySelector(".market-event")).toHaveClass("unread");
});

test("market flags a degraded feed when the primary source is not serving", async () => {
  window.history.replaceState({}, "", "/market");
  const payload = marketSnapshotPayload();
  payload.snapshot.source = "gate";
  global.fetch = marketFetch({ snapshot: payload });

  await act(async () => {
    await import("./main.jsx");
  });

  // 配置里 binance 是第一顺位，实际服务的是 gate，页面必须显式说明降级了。
  const chip = await screen.findByText("gate · 已降级");
  expect(chip).toHaveAttribute("title", "主源 binance 不可用，已降级到 gate");
  expect(screen.getByText("binance → gate")).toBeInTheDocument();
});

test("market disables the analyze action when no commentary agent is configured", async () => {
  window.history.replaceState({}, "", "/market");
  const base = marketFetch();
  global.fetch = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.includes("/api/market/overview")) {
      return response({
        enabled: true,
        sources: ["binance"],
        feed: [],
        commentary_agent: "",
        commentary_enabled: true,
        symbols: [{ symbol: "BTC/USDT", alias: "比特币", last_price: 1, change_pct_24h: 0, timeframes: [] }],
        events: { unread: 0, latest_seq: 0, last_read_seq: 0 },
      });
    }
    return base(url, options);
  });

  await act(async () => {
    await import("./main.jsx");
  });

  // 点了才报 400 是坏体验：没配好就直接禁用并说清去哪里配。
  const button = await screen.findByRole("button", { name: /让 Agent 分析一次/ });
  expect(button).toBeDisabled();
  expect(screen.getByText(/慢层还没指定 Agent/)).toBeInTheDocument();
});
