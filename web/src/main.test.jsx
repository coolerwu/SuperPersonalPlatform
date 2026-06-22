import "@testing-library/jest-dom/vitest";
import { act } from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("LoginPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
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

  it("opens the multidisciplinary critique matrix from the sidebar", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/critique");
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      constructor() {
        this.readyState = MockWebSocket.CONNECTING;
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.();
        }, 0);
      }
      close() {}
    }
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path) => {
        if (path === "/api/auth/me") {
          return { ok: true, json: async () => ({ authenticated: true }) };
        }
        if (path === "/api/critique/disciplines") {
          return { ok: true, json: async () => ({ disciplines: [] }) };
        }
        if (path === "/api/critique/runs") {
          return { ok: true, json: async () => ({ runs: [] }) };
        }
        return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
      })
    );

    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    expect(await screen.findByRole("button", { name: "多维批判" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("columnheader", { name: "核心假设" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "多维批判" })).not.toBeInTheDocument();
  });

  it("removes self-development and terminal navigation", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path) => {
        if (path === "/api/auth/me") {
          return { ok: true, json: async () => ({ authenticated: true }) };
        }
        return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
      })
    );

    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    expect(await screen.findByRole("button", { name: "Agent" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "自开发" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "终端" })).not.toBeInTheDocument();
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
    expect(screen.getByRole("navigation", { name: "Agent 工作区" })).toHaveClass("agent-mode-rail");
    expect(screen.getByRole("button", { name: "对话" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("运行信息")).toBeInTheDocument();
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
                  mode: "prompt",
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
    await user.selectOptions(screen.getByLabelText(/运行模式/), "agent");
    await user.click(screen.getByRole("button", { name: /^保存$/ }));

    const saveCall = fetch.mock.calls.find(
      ([path, options]) => path === "/api/agents/config" && options?.method === "PUT"
    );
    const payload = JSON.parse(saveCall[1].body);
    expect(payload.common_skill_tools).toEqual(["list_skill", "read_skill"]);
    expect(payload.agents[0].skill_ids).toEqual(["common:writing"]);
    expect(payload.models[0].mode).toBe("agent");
    expect(fetch).toHaveBeenCalledWith(
      "/api/agents/config",
      expect.objectContaining({
        method: "PUT",
        body: expect.stringContaining("视觉模型")
      })
    );
  });

  it("edits custom Agent rows from the Agent management tab", async () => {
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
              default_agent_id: "agent-1",
              agents: [{ id: "agent-1", name: "Agent", model: { has_api_key: true } }]
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
              skills: [],
              default_model_id: "fast",
              default_agent_id: "agent-1",
              models: [{ id: "fast", name: "快速模型", base_url: "", model: "fast-chat", temperature: 0.2, supports_images: false }],
              agents: [
                { id: "agent-1", name: "Agent", model_id: "fast", system_prompt: "You are terse.", skill_ids: [] },
                { id: "agent-2", name: "Second", model_id: "fast", system_prompt: "You are helpful.", skill_ids: [] }
              ]
            })
          };
        }
        return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
      })
    );

    const user = userEvent.setup();
    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    await user.click(await screen.findByRole("button", { name: /Agent 管理/ }));
    const idInput = await screen.findByDisplayValue("agent-2");
    await user.clear(idInput);
    await user.type(idInput, "agent-renamed");
    const nameInput = screen.getByDisplayValue("Second");
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed Agent");
    await user.click(screen.getByRole("button", { name: /^保存$/ }));

    const saveCall = fetch.mock.calls.find(
      ([path, options]) => path === "/api/agents/config" && options?.method === "PUT"
    );
    const payload = JSON.parse(saveCall[1].body);
    expect(payload.agents[1]).toMatchObject({
      id: "agent-renamed",
      name: "Renamed Agent"
    });
  });

  it("does not save a global default Agent when editing Agent IDs", async () => {
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
              default_agent_id: "agent-1",
              agents: [{ id: "agent-1", name: "Agent", model: { has_api_key: true } }]
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
              skills: [],
              default_model_id: "fast",
              default_agent_id: "agent-1",
              models: [{ id: "fast", name: "快速模型", base_url: "", model: "fast-chat", temperature: 0.2, supports_images: false }],
              agents: [
                { id: "agent-1", name: "Agent", model_id: "fast", system_prompt: "You are terse.", skill_ids: [] }
              ]
            })
          };
        }
        return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
      })
    );

    const user = userEvent.setup();
    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    await user.click(await screen.findByRole("button", { name: /Agent 管理/ }));
    const idInput = await screen.findByDisplayValue("agent-1");
    await user.clear(idInput);
    await user.type(idInput, "agent-renamed");
    await user.click(screen.getByRole("button", { name: /^保存$/ }));

    const saveCall = fetch.mock.calls.find(
      ([path, options]) => path === "/api/agents/config" && options?.method === "PUT"
    );
    const payload = JSON.parse(saveCall[1].body);
    expect(payload).not.toHaveProperty("default_agent_id");
    expect(payload.agents[0].id).toBe("agent-renamed");
  });

  it("keeps Agent ID and name editable during Chinese IME composition", async () => {
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
              default_agent_id: "agent-1",
              agents: [{ id: "agent-1", name: "Agent", model: { has_api_key: true } }]
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
              skills: [],
              default_model_id: "fast",
              default_agent_id: "agent-1",
              models: [{ id: "fast", name: "快速模型", base_url: "", model: "fast-chat", temperature: 0.2, supports_images: false }],
              agents: [
                { id: "agent-1", name: "Agent", model_id: "fast", system_prompt: "You are terse.", skill_ids: [] }
              ]
            })
          };
        }
        return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
      })
    );

    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    await screen.findByRole("button", { name: /Agent 管理/ });
    fireEvent.click(screen.getByRole("button", { name: /Agent 管理/ }));
    const idInput = await screen.findByDisplayValue("agent-1");
    fireEvent.compositionStart(idInput);
    fireEvent.change(idInput, { target: { value: "理财助手" } });
    expect(idInput).toHaveValue("理财助手");

    const nameInput = screen.getByDisplayValue("Agent");
    fireEvent.compositionStart(nameInput);
    fireEvent.change(nameInput, { target: { value: "理财顾问" } });
    expect(nameInput).toHaveValue("理财顾问");
  });

  it("updates the Agent bound to a WeChat channel account", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.replaceState({}, "", "/channels");
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
              agents: [
                { id: "assistant", name: "个人助理" },
                { id: "wife-agent", name: "妻子助理" }
              ]
            })
          };
        }
        if (path === "/api/channels/wechat/accounts") {
          return {
            ok: true,
            json: async () => ({
              accounts: [{
                id: "wife",
                name: "wife",
                default_agent_id: "assistant",
                status: { running: false, login_state: "stopped", user: "" }
              }]
            })
          };
        }
        if (path === "/api/channels/wechat/accounts/wife" && options?.method === "PUT") {
          return {
            ok: true,
            json: async () => ({
              ok: true,
              account: {
                id: "wife",
                name: "wife",
                default_agent_id: "wife-agent",
                status: { running: false, login_state: "stopped", user: "" }
              }
            })
          };
        }
        return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
      })
    );

    const user = userEvent.setup();
    vi.resetModules();
    await act(async () => {
      await import("./main.jsx");
    });

    const select = await screen.findByLabelText("wife 绑定 Agent");
    await user.selectOptions(select, "wife-agent");

    const updateCall = fetch.mock.calls.find(
      ([path, options]) => path === "/api/channels/wechat/accounts/wife" && options?.method === "PUT"
    );
    expect(JSON.parse(updateCall[1].body)).toEqual({ default_agent_id: "wife-agent" });
    expect(select).toHaveValue("wife-agent");
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
                id: "common:research",
                name: "研究",
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
                skill_ids: ["common:research"]
              }]
            })
          };
        }
        if (String(path).startsWith("/api/agents/skills/content")) {
          if (options?.method === "PUT") {
            return { ok: true, json: async () => ({ ok: true }) };
          }
          return { ok: true, json: async () => ({ id: "common:research", content: "# 研究\n旧内容" }) };
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
    await waitFor(() => expect(markdownEditor).toHaveValue("# 研究\n旧内容"));
    await user.clear(markdownEditor);
    await user.type(markdownEditor, "# 研究\n新内容");
    await user.click(await screen.findByLabelText(/查看持仓/));
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
      id: "common:research",
      content: "# 研究\n新内容",
      name: "common:research",
      tools: { profile: "default", allow: ["list_portfolio_holdings"], deny: [] },
      agent_id: null
    });
  });
});
