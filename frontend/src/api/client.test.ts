import { afterEach, expect, it, vi } from "vitest";
import { ApiError, apiRequest } from "./client";

afterEach(() => vi.unstubAllGlobals());
it("handles empty logout response", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
  );
  expect(await apiRequest("/auth/logout", { method: "POST" })).toBeUndefined();
});
it("preserves status and request ID", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { code: "unavailable", message: "Вход временно недоступен" },
            request_id: "trace",
          }),
          { status: 503 },
        ),
      ),
  );
  await expect(apiRequest("/auth/login")).rejects.toMatchObject({
    status: 503,
    requestId: "trace",
  });
});
it("network failure is not invalid credentials", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
  await expect(apiRequest("/auth/login")).rejects.toBeInstanceOf(ApiError);
  await expect(apiRequest("/auth/login")).rejects.toMatchObject({
    status: 0,
    code: "network_error",
  });
});
it("announces expired authentication", async () => {
  const expired = vi.fn();
  window.addEventListener("acti:unauthorized", expired);
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
  await expect(apiRequest("/users/me")).rejects.toMatchObject({ status: 401 });
  expect(expired).toHaveBeenCalledOnce();
  window.removeEventListener("acti:unauthorized", expired);
});
