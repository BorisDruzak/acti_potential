import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OriginalActions } from "./OriginalActions";
import type { FileVersion } from "../engine/schema";
vi.mock("../auth/AuthProvider", () => ({useAuth: () => ({user: {csrf_token: "csrf"}})}));
afterEach(cleanup);
it("aligns each named original with its actions without another chooser", () => {
  render(<OriginalActions files={[{id: "a", name: "letter.docx", version: 1}, {id: "b", name: "appendix.pdf", version: 1}] as FileVersion[]} canDownload mode="editor" csrfToken=""/>);
  expect(screen.queryByRole("button", {name: "Открыть в Word (просмотр)"})).toBeNull();
  for (const name of ["letter.docx", "appendix.pdf"]) {
    const row = screen.getByText(name).closest(".file-line")!;
    expect(within(row as HTMLElement).getByRole("button", {name: "Скачать"})).toBeTruthy();
  }
  expect(screen.queryByRole("button", {name: "Открыть оригинал"})).toBeNull();
});
it("does not expose original actions when downloading is denied", () => {
  render(<OriginalActions files={[{id: "a", name: "letter.docx", version: 1}] as FileVersion[]} canDownload={false} mode="viewer" csrfToken=""/>);
  expect(screen.getByText("letter.docx")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});
