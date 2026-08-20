import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import { act } from "react";
import { beforeEach, test, vi, expect } from "vitest";


beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  global.fetch = vi.fn(async (url) => {
    if (String(url).endsWith("/api/auth/me")) {
      return { ok: true, json: async () => ({ authenticated: true }) };
    }
    if (String(url).endsWith("/api/runs")) {
      return { ok: true, json: async () => ({ runs: [] }) };
    }
    return { ok: true, json: async () => ({}) };
  });
});


test("renders the DeepAgent console shell", async () => {
  await act(async () => {
    await import("./main.jsx");
  });

  expect(await screen.findByText("DeepAgent")).toBeInTheDocument();
  expect(await screen.findByText("Runs")).toBeInTheDocument();
  expect(await screen.findByText("微信")).toBeInTheDocument();
});
