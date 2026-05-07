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
        if (path === "/api/system/terminal/sessions/list") {
          return {
            ok: true,
            json: async () => ({
              sessions: [
                {
                  name: "terminal-2026-05-06T143012-abcdef12.jsonl",
                  path: "/workspace/terminal/sessions/terminal-2026-05-06T143012-abcdef12.jsonl",
                  size: 12,
                  modified_at: "2026-05-06T14:30:12"
                }
              ]
            })
          };
        }
        if (path === "/api/system/terminal/sessions/delete") {
          return {
            ok: true,
            json: async () => ({ ok: true })
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

    await userEvent.click(
      await screen.findByRole("button", {
        name: /删除 terminal-2026-05-06T143012-abcdef12\.jsonl/
      })
    );

    expect(fetch).toHaveBeenCalledWith(
      "/api/system/terminal/sessions/delete",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ name: "terminal-2026-05-06T143012-abcdef12.jsonl" })
      })
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
          data: JSON.stringify({ type: "assistant_message", content: "你好，我是 Agent" })
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
    expect(await screen.findByText("你好，我是 Agent")).toBeInTheDocument();
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
    expect(await screen.findByText("请先在配置页添加 Agent")).toBeInTheDocument();
  });

  it("edits Agent workspace config from the config tab", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/agents");
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      constructor() {
        setTimeout(() => this.onopen?.(), 0);
      }
      close() {}
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

    await user.click(await screen.findByRole("button", { name: /配置/ }));
    await user.clear((await screen.findAllByDisplayValue("快速模型"))[0]);
    await user.type(screen.getByLabelText(/显示名/), "视觉模型");
    await user.click(screen.getByRole("button", { name: /保存到 workspace/ }));

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
});
