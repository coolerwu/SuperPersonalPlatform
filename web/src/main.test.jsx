import "@testing-library/jest-dom/vitest";
import { act } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const xtermState = vi.hoisted(() => ({
  terminals: [],
  fitAddons: []
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: class MockTerminal {
    constructor() {
      this.cols = 120;
      this.rows = 34;
      this._dataHandler = null;
      xtermState.terminals.push(this);
    }

    loadAddon() {}

    open(element) {
      this.element = element;
    }

    onData(handler) {
      this._dataHandler = handler;
    }

    write(data) {
      this.element.textContent = `${this.element.textContent}${data}`;
    }

    focus() {}

    dispose() {}

    emitData(data) {
      this._dataHandler?.(data);
    }
  }
}));

vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class MockFitAddon {
    constructor() {
      xtermState.fitAddons.push(this);
    }

    fit() {
      this.fitted = true;
    }
  }
}));

describe("LoginPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    xtermState.terminals.length = 0;
    xtermState.fitAddons.length = 0;
    document.body.innerHTML = "";
    window.history.replaceState({}, "", "/");
  });

  it("enables login only after a token is entered", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ authenticated: false })
      })
    );

    const user = userEvent.setup();
    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    const button = await screen.findByRole("button", { name: /进入平台/ });
    expect(button).toBeDisabled();

    await user.type(screen.getByLabelText("访问 Token"), "secret-token");

    await waitFor(() => expect(button).toBeEnabled());
  });

  it("loads unified logs from the system logs tab", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/system");
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
      configurable: true,
      get() {
        return 1000;
      }
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path, options) => {
        if (path === "/api/auth/me") {
          return {
            ok: true,
            json: async () => ({ authenticated: true })
          };
        }
        if (path === "/api/system/config/read") {
          return {
            ok: true,
            json: async () => ({
              path: "/workspace/config.yaml",
              content: "auth:\n  token: secret-token\n"
            })
          };
        }
        if (path === "/api/system/logs/list") {
          return {
            ok: true,
            json: async () => ({
              logs: [
                {
                  name: "platform-2026-05-06.log",
                  path: "/workspace/logs/platform-2026-05-06.log",
                  size: 9,
                  modified_at: "2026-05-06T09:00:00"
                }
              ]
            })
          };
        }
        if (path === "/api/system/logs/read") {
          return {
            ok: true,
            json: async () => ({
              name: "platform-2026-05-06.log",
              path: "/workspace/logs/platform-2026-05-06.log",
              size: 9,
              modified_at: "2026-05-06T09:00:00",
              content: "hello log",
              truncated: false
            })
          };
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: "not found" })
        };
      })
    );

    const user = userEvent.setup();
    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    await user.click(await screen.findByRole("tab", { name: /日志/ }));

    await screen.findByText("hello log");
    expect(screen.getAllByText("platform-2026-05-06.log").length).toBeGreaterThan(0);
    expect(await screen.findByText("hello log")).toBeInTheDocument();
    expect(screen.getByTestId("log-viewer-shell")).toHaveClass("log-viewer-shell");
    await waitFor(() => expect(screen.getByText("hello log").scrollTop).toBe(1000));
  });

  it("does not render duplicate page titles above menu pages", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/system");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path, options) => {
        if (path === "/api/auth/me") {
          return {
            ok: true,
            json: async () => ({ authenticated: true })
          };
        }
        if (path === "/api/system/config/read") {
          return {
            ok: true,
            json: async () => ({
              path: "/workspace/config.yaml",
              content: "auth:\n  token: secret-token\n"
            })
          };
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: "not found" })
        };
      })
    );

    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    await screen.findByRole("tab", { name: /配置/ });
    expect(screen.queryByText("系统运维")).not.toBeInTheDocument();
  });

  it("renders the terminal menu and connects to the backend terminal", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/terminal");
    const sockets = [];
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;

      constructor(url) {
        this.url = url;
        this.readyState = MockWebSocket.CONNECTING;
        this.sent = [];
        sockets.push(this);
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.();
          this.onmessage?.({ data: JSON.stringify({ type: "output", data: "terminal ready\n" }) });
        }, 0);
      }

      send(data) {
        this.sent.push(data);
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.();
      }
    }
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal(
      "ResizeObserver",
      class MockResizeObserver {
        observe() {}
        disconnect() {}
      }
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path, options) => {
        if (path === "/api/auth/me") {
          return {
            ok: true,
            json: async () => ({ authenticated: true })
          };
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: "not found" })
        };
      })
    );

    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    expect(await screen.findByRole("button", { name: "终端" })).toBeInTheDocument();
    expect(await screen.findByText("terminal ready")).toBeInTheDocument();
    expect(sockets[0].url).toContain("/api/system/terminal/connect");
    expect(JSON.parse(sockets[0].sent[0])).toEqual({ type: "resize", cols: 120, rows: 34 });

    xtermState.terminals[0].emitData("ls\r");

    expect(JSON.parse(sockets[0].sent.at(-1))).toEqual({ type: "input", data: "ls\r" });
    const navButtons = screen.getAllByRole("button").map((button) => button.textContent);
    expect(navButtons.indexOf("终端")).toBeLessThan(navButtons.indexOf("系统"));
    expect(screen.queryByText("历史会话")).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/system/terminal/sessions/list",
      expect.anything()
    );
  });

  it("shows the Agent chat without model controls and sends text plus images", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/agents");
    const sockets = [];
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;

      constructor(url) {
        this.url = url;
        this.readyState = MockWebSocket.CONNECTING;
        this.sent = [];
        sockets.push(this);
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.();
          this.onmessage?.({ data: JSON.stringify({ type: "status", status: "connected" }) });
        }, 0);
      }

      send(data) {
        this.sent.push(data);
        this.onmessage?.({ data: JSON.stringify({ type: "status", status: "running" }) });
        this.onmessage?.({
          data: JSON.stringify({
            type: "checkpoint",
            stage: "goal",
            title: "task 目标已确认",
            detail: "回复用户问候"
          })
        });
        this.onmessage?.({
          data: JSON.stringify({ type: "assistant_message", content: "1. **重点**：你好，我是 `Agent`" })
        });
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.();
      }
    }
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path, options) => {
        if (path === "/api/auth/me") {
          return {
            ok: true,
            json: async () => ({ authenticated: true })
          };
        }
        if (path === "/api/agents/options") {
          return {
            ok: true,
            json: async () => ({
              default_agent_id: "assistant",
              agents: [
                {
                  id: "assistant",
                  name: "个人助理",
                  model_id: "fast",
                  model: {
                    id: "fast",
                    name: "快速模型",
                    model: "fast-chat",
                    base_url: "https://llm.example.test/v1",
                    supports_images: true,
                    has_api_key: true
                  }
                }
              ]
            })
          };
        }
        if (path === "/api/agents/config") {
          return {
            ok: true,
            json: async () => ({
              path: "/workspace/config.yaml",
              default_agent_id: "assistant",
              default_model_id: "fast",
              models: [],
              agents: []
            })
          };
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: "not found" })
        };
      })
    );

    const user = userEvent.setup();
    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    expect(await screen.findByRole("button", { name: "Agent" })).toBeInTheDocument();
    expect(document.querySelector("main.content")).toHaveClass("content-agents");
    expect(screen.queryByText("模型")).not.toBeInTheDocument();
    expect(screen.queryByText("你是一个直接、可靠的个人助理。")).not.toBeInTheDocument();

    const file = new File(["image"], "agent.png", { type: "image/png" });
    await user.upload(document.querySelector('input[type="file"]'), file);
    expect(await screen.findByAltText("agent.png")).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText(/输入消息/), "你好");
    await user.click(screen.getByRole("button", { name: /发送/ }));

    const payload = JSON.parse(sockets[0].sent[0]);
    expect(payload.type).toBe("message");
    expect(payload.agent_id).toBe("assistant");
    expect(payload.content).toBe("你好");
    expect(payload.model_id).toBeUndefined();
    expect(payload.images).toHaveLength(1);
    expect(payload.images[0].mime_type).toBe("image/png");
    expect(await screen.findByText("重点")).toBeInTheDocument();
    expect(screen.getByText("重点").tagName).toBe("STRONG");
    expect(screen.getAllByText("Agent").some((node) => node.tagName === "CODE")).toBe(true);
  });

  it("keeps the Agent menu available and shows an empty state without agents", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/agents");
    class ClosedWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      constructor() {
        setTimeout(() => this.onclose?.(), 0);
      }
      close() {}
    }
    vi.stubGlobal("WebSocket", ClosedWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path, options) => {
        if (path === "/api/auth/me") {
          return {
            ok: true,
            json: async () => ({ authenticated: true })
          };
        }
        if (path === "/api/agents/options") {
          return {
            ok: true,
            json: async () => ({
              default_agent_id: "",
              agents: []
            })
          };
        }
        if (path === "/api/agents/config") {
          return {
            ok: true,
            json: async () => ({
              path: "/workspace/config.yaml",
              default_agent_id: "",
              default_model_id: "",
              models: [],
              agents: []
            })
          };
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: "not found" })
        };
      })
    );

    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    expect(await screen.findByRole("button", { name: "Agent" })).toBeInTheDocument();
    expect(await screen.findByText("请先在 Agent 管理中添加 Agent")).toBeInTheDocument();
  });

  it("shows self-dev create form by default without the old step indicator", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/self-dev");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path) => {
        if (path === "/api/auth/me") {
          return { ok: true, json: async () => ({ authenticated: true }) };
        }
        if (path === "/api/agents/options") {
          return {
            ok: true,
            json: async () => ({
              default_agent_id: "coder",
              agents: [{ id: "coder", name: "编码 Agent" }]
            })
          };
        }
        if (path === "/api/self-dev/tasks") {
          return { ok: true, json: async () => ({ tasks: [] }) };
        }
        return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
      })
    );

    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    expect(await screen.findByRole("button", { name: "自开发" })).toBeInTheDocument();
    expect(await screen.findByText("新建开发任务")).toBeInTheDocument();
    expect(screen.queryByText("选择或创建一个任务")).not.toBeInTheDocument();
    expect(screen.queryByText("创建")).not.toBeInTheDocument();
    expect(screen.queryByText("审查")).not.toBeInTheDocument();
  });

  it("opens selected self-dev task in tabs with real status instead of fake progress", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/self-dev");
    const task = {
      id: "task-1",
      status: "running",
      goal: "重构自开发页面",
      branch: "agent/self-dev-task-1",
      created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
      updated_at: new Date().toISOString(),
      events: [
        { type: "run", goal: "重构自开发页面", timestamp: "2026-05-06T09:00:00" },
        { type: "log", level: "info", message: "正在执行真实任务" }
      ],
      diff: "diff --git a/web/src/main.jsx b/web/src/main.jsx\n--- a/web/src/main.jsx\n+++ b/web/src/main.jsx\n@@ -1 +1 @@\n-old\n+new\n"
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path) => {
        if (path === "/api/auth/me") {
          return { ok: true, json: async () => ({ authenticated: true }) };
        }
        if (path === "/api/agents/options") {
          return {
            ok: true,
            json: async () => ({ default_agent_id: "coder", agents: [{ id: "coder", name: "编码 Agent" }] })
          };
        }
        if (path === "/api/self-dev/tasks") {
          return { ok: true, json: async () => ({ tasks: [task] }) };
        }
        if (path === "/api/self-dev/tasks/task-1") {
          return { ok: true, json: async () => ({ task }) };
        }
        return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
      })
    );

    const user = userEvent.setup();
    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    await user.click(await screen.findByRole("button", { name: /重构自开发页面/ }));

    expect((await screen.findAllByText("运行中")).length).toBeGreaterThan(0);
    expect(screen.getByRole("tab", { name: /对话/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /文件变更/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /执行日志/ })).toBeInTheDocument();
    expect(screen.getByText(/实际状态/)).toBeInTheDocument();
    expect(screen.getByText(/运行时间/)).toBeInTheDocument();
    expect(screen.queryByText("分析需求")).not.toBeInTheDocument();
    expect(screen.queryByText("生成报告")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /文件变更/ }));
    expect(await screen.findByText("web/src/main.jsx")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /执行日志/ }));
    expect(await screen.findByText("正在执行真实任务")).toBeInTheDocument();
  });

  it("edits model config from the Agent model tab", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/agents");
    class ClosedWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      constructor() {
        setTimeout(() => this.onclose?.(), 0);
      }
      close() {}
    }
    vi.stubGlobal("WebSocket", ClosedWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path, options) => {
        if (path === "/api/auth/me") {
          return {
            ok: true,
            json: async () => ({ authenticated: true })
          };
        }
        if (path === "/api/agents/options") {
          return {
            ok: true,
            json: async () => ({
              default_agent_id: "assistant",
              agents: [
                {
                  id: "assistant",
                  name: "个人助理",
                  model: {
                    id: "fast",
                    name: "快速模型",
                    model: "fast-chat",
                    has_api_key: true,
                    supports_images: true
                  }
                }
              ]
            })
          };
        }
        if (path === "/api/sessions") {
          return {
            ok: true,
            json: async () => ({ sessions: [] })
          };
        }
        if (path === "/api/agents/config") {
          if (options?.method === "PUT") {
            return {
              ok: true,
              json: async () => ({ ok: true })
            };
          }
          return {
            ok: true,
            json: async () => ({
              path: "/workspace/config.yaml",
              common_skill_tools: ["list_skill", "read_skill"],
              default_model_id: "fast",
              default_agent_id: "assistant",
              models: [
                {
                  id: "fast",
                  name: "快速模型",
                  base_url: "https://llm.example.test/v1",
                  model: "fast-chat",
                  temperature: 0.2,
                  supports_images: true,
                  has_api_key: true,
                  api_key_mask: "********"
                }
              ],
              agents: [
                {
                  id: "assistant",
                  name: "个人助理",
                  model_id: "fast",
                  system_prompt: "You are concise.",
                  skill_ids: ["common:writing"]
                }
              ]
            })
          };
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: "not found" })
        };
      })
    );

    const user = userEvent.setup();
    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    await user.click(await screen.findByRole("button", { name: /模型配置/ }));
    expect(screen.queryByText("工具权限")).not.toBeInTheDocument();
    await user.click(await screen.findByTitle("展开"));
    await user.clear((await screen.findAllByDisplayValue("快速模型"))[0]);
    await user.type(screen.getByLabelText(/显示名/), "视觉模型");
    await user.click(screen.getByRole("button", { name: /^保存$/ }));

    const saveCall = fetch.mock.calls.find(
      ([path, options]) => path === "/api/agents/config" && options?.method === "PUT"
    );
    const payload = JSON.parse(saveCall[1].body);
    expect(payload.common_skill_tools).toEqual(["list_skill", "read_skill"]);
    expect(payload.agents[0].skill_ids).toEqual(["common:writing"]);
    expect(fetch).toHaveBeenCalledWith(
      "/api/agents/config",
      expect.objectContaining({
        method: "PUT",
        body: expect.stringContaining("视觉模型")
      })
    );
  });

  it("selects tools from the Skill management tab", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/agents");
    class ClosedWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      constructor() {
        setTimeout(() => this.onclose?.(), 0);
      }
      close() {}
    }
    vi.stubGlobal("WebSocket", ClosedWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path, options) => {
        if (path === "/api/auth/me") {
          return { ok: true, json: async () => ({ authenticated: true }) };
        }
        if (path === "/api/agents/options") {
          return {
            ok: true,
            json: async () => ({
              default_agent_id: "assistant",
              agents: [{ id: "assistant", name: "个人助理", model: { has_api_key: true } }]
            })
          };
        }
        if (path === "/api/sessions") {
          return { ok: true, json: async () => ({ sessions: [] }) };
        }
        if (path === "/api/agents/config") {
          if (options?.method === "PUT") {
            return { ok: true, json: async () => ({ ok: true }) };
          }
          return {
            ok: true,
            json: async () => ({
              path: "/workspace/config.yaml",
              common_skill_tools: [],
              tools: { profile: "default", allow: [], deny: [] },
              skills: [{
                id: "common:self-dev",
                name: "自开发",
                tools: { profile: "default", allow: [], deny: [] }
              }],
              default_model_id: "fast",
              default_agent_id: "assistant",
              models: [{ id: "fast", name: "快速模型", base_url: "", model: "fast-chat", temperature: 0.2, supports_images: false }],
              agents: [{
                id: "assistant",
                name: "个人助理",
                model_id: "fast",
                system_prompt: "You are concise.",
                skill_ids: ["common:self-dev"]
              }]
            })
          };
        }
        if (String(path).startsWith("/api/agents/skills/content")) {
          if (options?.method === "PUT") {
            return { ok: true, json: async () => ({ ok: true }) };
          }
          return { ok: true, json: async () => ({ id: "common:self-dev", content: "# 自开发\n旧内容" }) };
        }
        return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
      })
    );

    const user = userEvent.setup();
    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    await user.click(await screen.findByRole("button", { name: /Skill 管理/ }));
    expect(await screen.findByText("Skill 文件")).toBeInTheDocument();
    expect(screen.queryByText(/禁用工具/)).not.toBeInTheDocument();
    const markdownEditor = await screen.findByLabelText(/Markdown 内容/);
    await waitFor(() => expect(markdownEditor).toHaveValue("# 自开发\n旧内容"));
    await user.clear(markdownEditor);
    await user.type(markdownEditor, "# 自开发\n新内容");
    await user.click(await screen.findByLabelText(/仓库搜索/));
    await user.click(screen.getByRole("button", { name: /^保存$/ }));

    const saveCall = fetch.mock.calls.find(
      ([path, options]) => path === "/api/agents/config" && options?.method === "PUT"
    );
    const payload = JSON.parse(saveCall[1].body);
    expect(payload.skills[0].tools).toBeUndefined();
    expect(payload.agents[0].tools).toBeUndefined();
    const skillContentCall = fetch.mock.calls.find(
      ([path, options]) => path === "/api/agents/skills/content" && options?.method === "PUT"
    );
    expect(JSON.parse(skillContentCall[1].body)).toEqual({
      id: "common:self-dev",
      content: "# 自开发\n新内容",
      name: "common:self-dev",
      tools: { profile: "default", allow: ["repo_search"], deny: [] },
      agent_id: null
    });
  });
});
