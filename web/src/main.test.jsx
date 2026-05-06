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
      vi.fn(async (path) => {
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
      vi.fn(async (path) => {
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
      vi.fn(async (path) => {
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
});
