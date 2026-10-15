import { afterEach, expect, it, vi } from "vitest";
import { apiDownload, apiUpload } from "./client";
afterEach(() => vi.unstubAllGlobals());
it("uploads the original bytes with CSRF and without a JSON content type", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ id: "file" })));
  vi.stubGlobal("fetch", fetch);
  const body = new Blob(["original"], { type: "application/pdf" });
  await apiUpload(
    "/documents/id/files?filename=original.pdf&revision=1",
    body,
    "csrf",
  );
  const options = fetch.mock.calls[0][1];
  expect(options.body).toBe(body);
  expect(options.headers.get("Content-Type")).toBe("application/pdf");
  expect(options.headers.get("X-CSRF-Token")).toBe("csrf");
});
it("downloads binary bodies and propagates forbidden responses", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(new Response("bytes"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { message: "Нет доступа" } }), {
          status: 403,
        }),
      ),
  );
  expect(await (await apiDownload("/files/id/download")).text()).toBe("bytes");
  await expect(apiDownload("/files/id/download")).rejects.toMatchObject({
    status: 403,
  });
});
