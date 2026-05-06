import "@testing-library/jest-dom/vitest";
import { act } from "react";
import { screen, waitFor } from "@testing-library/react";
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
  });
});
