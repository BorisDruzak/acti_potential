import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OfficeOpen } from "./OfficeOpen";

afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();});

it("issues a CSRF protected exact-file handoff and opens the returned native URI", async () => {
  const uri = `ms-word:ofv|u|${window.location.origin}/api/v1/office/files/file/letter.docx?ticket=opaque`;
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({uri, expires_at: "2099-01-01"})));
  vi.stubGlobal("fetch", fetch);
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<OfficeOpen file={{id: "file", name: "letter.docx"}} mode="viewer" csrfToken="csrf"/>);
  fireEvent.click(screen.getByRole("button", {name: "Открыть в Word (просмотр)"}));
  await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
  expect(fetch.mock.calls[0][0]).toBe("/api/v1/files/file/office-open");
  expect(fetch.mock.calls[0][1].headers.get("X-CSRF-Token")).toBe("csrf");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({application: "word", mode: "viewer"});
  expect((click.mock.instances[0] as unknown as HTMLAnchorElement).href).toBe(uri);
});

it("shows an access error and never launches an unapproved URI", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({uri: "file:///private.docx"}))));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<OfficeOpen file={{id: "file", name: "letter.docx"}} mode="editor" csrfToken="csrf"/>);
  fireEvent.click(screen.getByRole("button", {name: "Открыть в LibreOffice (просмотр)"}));
  expect((await screen.findByRole("alert")).textContent).toContain("Ссылка открытия недоступна");
  expect(click).not.toHaveBeenCalled();
});

it("does not offer Office handlers for unsupported originals", () => {
  render(<OfficeOpen file={{id: "file", name: "archive.zip"}} mode="viewer" csrfToken="csrf"/>);
  expect(screen.queryByRole("button")).toBeNull();
});
