import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthProvider";

function Probe() {
  return <span>{useAuth().status}</span>;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("distinguishes unauthenticated from unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { code: "unauthorized", message: "Войдите" },
          }),
          { status: 401 },
        ),
      ),
  );
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
  await waitFor(() => expect(screen.getByText("anonymous")).toBeTruthy());
});
it("shows service unavailability separately", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
  await waitFor(() => expect(screen.getByText("unavailable")).toBeTruthy());
});
