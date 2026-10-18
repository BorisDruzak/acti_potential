import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OriginalActions } from "./OriginalActions";
import type { FileVersion } from "../engine/schema";
vi.mock("../auth/AuthProvider", () => ({useAuth: () => ({user: {csrf_token: "csrf"}})}));
afterEach(cleanup);
it("requires an explicit file choice when there are multiple originals", () => {
  render(<OriginalActions files={[{id: "a", name: "letter.docx", version: 1}, {id: "b", name: "appendix.pdf", version: 1}] as FileVersion[]} canDownload mode="editor" csrfToken=""/>);
  expect(screen.queryByRole("button", {name: "Открыть в Word"})).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "Открыть оригинал"}));
  const dialog = screen.getByRole("dialog", {name: "Выбрать оригинал"});
  expect(dialog.textContent).toContain("appendix.pdf");
  expect(dialog.parentElement?.parentElement).toBe(document.body);
});
it("does not expose original actions when downloading is denied", () => {
  render(<OriginalActions files={[{id: "a", name: "letter.docx", version: 1}] as FileVersion[]} canDownload={false} mode="viewer" csrfToken=""/>);
  expect(screen.queryByRole("button")).toBeNull();
});
