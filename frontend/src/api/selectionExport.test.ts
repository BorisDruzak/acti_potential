import { afterEach, expect, it, vi } from "vitest";
import { saveSelectedExport } from "./client";

afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals();});

it("posts only the selected IDs and requested columns with CSRF before saving a binary file", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("selected-bytes"));
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("URL", class extends URL {static createObjectURL() {return "blob:selection";} static revokeObjectURL() {}});
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  await saveSelectedExport(["b", "a"], "xlsx", ["title"], "editor", "csrf");
  expect(fetch.mock.calls[0][0]).toBe("/api/v1/documents/export-selection");
  const options = fetch.mock.calls[0][1];
  expect(options.method).toBe("POST");
  expect(new Headers(options.headers).get("X-CSRF-Token")).toBe("csrf");
  expect(JSON.parse(options.body)).toEqual({document_ids: ["b", "a"], format: "xlsx", columns: ["title"], mode: "editor"});
  expect(click).toHaveBeenCalledTimes(1);
  expect((click.mock.instances[0] as unknown as HTMLAnchorElement).download).toBe("selected-documents.xlsx");
});
