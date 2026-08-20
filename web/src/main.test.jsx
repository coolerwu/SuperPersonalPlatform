import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, test, vi, expect } from "vitest";

function response(data) {
  return { ok: true, json: async () => data };
}

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
  global.fetch = vi.fn(async (url) => {
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
});

test("renders the DeepAgent console shell", async () => {
  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("DeepAgent")).toBeInTheDocument();
  expect((await screen.findAllByText("Runs")).length).toBeGreaterThan(1);
  expect(await screen.findByText("workspace/runs/index.json")).toBeInTheDocument();
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
    await vi.advanceTimersByTimeAsync(2600);
  });

  expect(screen.getByText("稳定结果内容")).toBeInTheDocument();
  expect(screen.queryByText("unknown")).not.toBeInTheDocument();
});
