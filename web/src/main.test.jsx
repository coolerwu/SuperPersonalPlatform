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
});
