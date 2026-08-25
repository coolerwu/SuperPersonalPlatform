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
