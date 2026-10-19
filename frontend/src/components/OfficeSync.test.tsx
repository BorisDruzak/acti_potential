import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FileActions, validSyncUri, type OfficeSyncStatus } from "./OfficeSync";
import { apiRequest, saveDownload } from "../api/client";
import { DynamicForm } from "../engine/DynamicForm";
import type { FileVersion } from "../engine/schema";

vi.mock("../api/client", async importOriginal => ({...await importOriginal<typeof import("../api/client")>(), apiRequest: vi.fn(), saveDownload: vi.fn()}));
const request = vi.mocked(apiRequest);
const file = {id: "logical-file", current_file_id: "bytes-2", name: "letter.docx", version: 1, size: 1200, mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", created_at: "2026-10-19"} satisfies FileVersion;
const base: OfficeSyncStatus = {
  file_id: file.id, current_file_id: "bytes-3", filename: file.name, revision: 3,
  can_edit: true, reason: null, state: "idle", session_id: null, last_seen_at: null,
  last_saved_at: null, lease_expires_at: null, editor: null,
};
beforeEach(() => {request.mockReset(); vi.mocked(saveDownload).mockReset(); request.mockResolvedValue(base);});
afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();});
const show = (mode: "editor" | "viewer" = "editor", sync = true) => render(<FileActions file={file} mode={mode} csrfToken="csrf" sync={sync}/>);

it("requires server capability, and readers never get a write action", async () => {
  request.mockResolvedValue({...base, can_edit: false, reason: "Нет права загрузки"});
  const {unmount} = show();
  await screen.findByText("Нет права загрузки");
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
  unmount();
  request.mockResolvedValue(base);
  show("viewer");
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.stringContaining("mode=viewer"), expect.anything()));
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
  expect(screen.getByRole("button", {name: "Открыть в Word (просмотр)"})).toBeTruthy();
});

it("launches only an exact one-use-ticket protocol, preserves CSRF, and does not invent editing or ACK", async () => {
  request.mockImplementation(async path => path === "/office-sync/launch" ? {uri: "acti-sync://launch?ticket=" + "a".repeat(43), session_id: "session", expires_at: "2099-01-01"} : base);
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  show();
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать в Word"}));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(request).toHaveBeenCalledWith("/office-sync/launch", {method: "POST", headers: {"X-CSRF-Token": "csrf"}, body: JSON.stringify({file_id: file.id})});
  expect(screen.queryByText("Редактируется")).toBeNull();
  expect(screen.queryByText("Сохранено в ACTI")).toBeNull();
  expect(screen.getByText(/Если Word не открылся/)).toBeTruthy();
  expect(document.body.textContent).not.toContain("a".repeat(43));
});

it("runs the launch handler again after the server permits a new session following an expired lease", async () => {
  let launches = 0;
  request.mockImplementation(async path => {
    if (path === "/office-sync/launch") {
      launches += 1;
      return {uri: "acti-sync://launch?ticket=" + "a".repeat(43), session_id: `session-${launches}`, expires_at: "2099-01-01"};
    }
    return {...base, state: "offline", session_id: "expired-session", lease_expires_at: "2020-01-01T00:00:00Z"};
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  show();
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать в Word"}));
  await waitFor(() => expect(launches).toBe(1));
  await waitFor(() => expect(screen.getByRole("button", {name: "Редактировать в Word"})).toHaveProperty("disabled", false));
  fireEvent.click(screen.getByRole("button", {name: "Редактировать в Word"}));
  await waitFor(() => expect(launches).toBe(2));
});

it("rejects arbitrary URLs without handing them to a native handler", async () => {
  request.mockImplementation(async path => path === "/office-sync/launch" ? {uri: "https://example.com/secret"} : base);
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  show(); fireEvent.click(await screen.findByRole("button", {name: "Редактировать в Word"}));
  expect((await screen.findByRole("alert")).textContent).toContain("допустимую ссылку");
  expect(click).not.toHaveBeenCalled();
  expect(validSyncUri("acti-sync://launch?ticket=" + "a".repeat(43) + "&origin=https://example.com")).toBe(false);
});

it("shows an installation/start fallback after a launch receives no client contact", async () => {
  vi.useFakeTimers();
  let launched = false;
  request.mockImplementation(async path => {
    if (path === "/office-sync/launch") {
      launched = true;
      return {uri: "acti-sync://launch?ticket=" + "b".repeat(43), session_id: "missing-client", expires_at: "2099-01-01"};
    }
    return launched ? {...base, can_edit: false, state: "opening", session_id: "missing-client", lease_expires_at: new Date(Date.now() + 60000).toISOString()} : base;
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  show(); await act(async () => {});
  await act(async () => {fireEvent.click(screen.getByRole("button", {name: "Редактировать в Word"}));});
  await act(async () => {await vi.advanceTimersByTimeAsync(35000);});
  expect(screen.getByText("ACTI Sync не подключился. Проверьте установку и запуск программы.")).toBeTruthy();
  expect(screen.getByRole("button", {name: "Открыть в Word (просмотр)"})).toBeTruthy();
  expect(screen.queryByText("Редактируется")).toBeNull();
});

it("does not present an old ACK as current after the lease/client contact expires", async () => {
  request.mockResolvedValue({...base, state: "saved", session_id: "session", last_saved_at: "2026-01-01T00:00:00Z", last_seen_at: "2026-01-01T00:00:00Z", lease_expires_at: "2026-01-01T00:01:00Z"});
  show();
  await screen.findByText("Нет связи — сохранение в ACTI не подтверждено");
  expect(screen.queryByText("Сохранено в ACTI")).toBeNull();
  expect(screen.getByText(/Последняя связь:/)).toBeTruthy();
});

it("keeps Word open/closed observation independent of the server save ACK", async () => {
  const now = new Date().toISOString();
  request.mockResolvedValue({...base, state: "saved", session_id: "session", document_open: false, opened_at: now, closed_at: now, last_saved_at: now, last_seen_at: now, lease_expires_at: new Date(Date.now() + 60000).toISOString()});
  show();
  await screen.findByText("Закрыт в Word");
  expect(screen.getByText("Сохранено в ACTI")).toBeTruthy();
  expect(screen.getByText(/Открыт:/)).toBeTruthy(); expect(screen.getByText(/Закрыт:/)).toBeTruthy();
});

it("shows a fresh server commit ACK, then removes it when contact with the API fails", async () => {
  vi.useFakeTimers();
  request.mockResolvedValue({...base, state: "saved", session_id: "session", last_saved_at: new Date().toISOString(), last_seen_at: new Date().toISOString(), lease_expires_at: new Date(Date.now() + 120000).toISOString()});
  show();
  await act(async () => {});
  expect(screen.getByText("Сохранено в ACTI")).toBeTruthy();
  request.mockRejectedValue(new Error("unreachable"));
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(screen.queryByText("Сохранено в ACTI")).toBeNull();
  expect(screen.getByText("Нет связи с ACTI. Состояние сохранения неизвестно.")).toBeTruthy();
  expect((screen.getByRole("button", {name: "Редактировать в Word"}) as HTMLButtonElement).disabled).toBe(true);
});

it("shares polling between the card header and its dynamic file field", async () => {
  vi.useFakeTimers();
  render(<><FileActions file={file} mode="editor" csrfToken="csrf"/><FileActions file={file} mode="editor" csrfToken="csrf"/></>);
  await act(async () => {});
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(request).toHaveBeenCalledTimes(2);
});

it("keeps one current original and downloads history using immutable byte IDs", async () => {
  request.mockImplementation(async path => path.includes("/history") ? {items: [{file_id: "immutable-old", revision: 1, size: 1024, created_at: "2026-01-01", created_by_name: "Редактор"}]} : base);
  show();
  await screen.findByRole("button", {name: "Редактировать в Word"});
  fireEvent.click(screen.getByRole("button", {name: "Скачать"}));
  expect(saveDownload).toHaveBeenCalledWith("bytes-3", "letter.docx", "editor");
  const history = screen.getByText("История версий").closest("details")!;
  history.open = true; fireEvent(history, new Event("toggle"));
  fireEvent.click(await screen.findByRole("button", {name: "Скачать редакцию 1"}));
  expect(saveDownload).toHaveBeenLastCalledWith("immutable-old", "letter.docx", "editor");
  expect(screen.getAllByRole("button", {name: "Редактировать в Word"})).toHaveLength(1);
});

it("polls every seven seconds without overlapping requests and stops/aborts on unmount", async () => {
  vi.useFakeTimers();
  const view = show();
  await act(async () => {});
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(request).toHaveBeenCalledTimes(2);
  const signal = request.mock.calls[1][1]?.signal;
  view.unmount();
  expect(signal?.aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(21000);
  expect(request).toHaveBeenCalledTimes(2);
});

it("retains unsaved DynamicForm values while the file status updates", async () => {
  vi.useFakeTimers();
  function Form() {
    const [values, setValues] = useState({title: "Исходное", original: [file.id]});
    return <DynamicForm schema={{sections: [{key: "main", title: "Карточка", fields: [{key: "title", label: "Название", type: "text", editable: true}, {key: "original", label: "Оригинал", type: "file", editable: true}]}]}}
      values={values} onChange={next => setValues(next as typeof values)} mode="edit" roles={["editor"]} files={[file]} officeSync={{csrfToken: "csrf"}}/>;
  }
  render(<Form/>);
  fireEvent.change(screen.getByRole("textbox", {name: "Название"}), {target: {value: "Несохранённый черновик"}});
  await act(async () => {});
  request.mockResolvedValue({...base, state: "conflict"});
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(screen.getByText("Конфликт: требуется действие")).toBeTruthy();
  expect((screen.getByRole("textbox", {name: "Название"}) as HTMLInputElement).value).toBe("Несохранённый черновик");
});

it("never launches or polls historical metadata revisions", () => {
  show("editor", false);
  expect(request).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
});

it("uploads a new binary version only to the explicitly selected original with CAS/hash and a server receipt", async () => {
  const digest = new Uint8Array(32).fill(0xab).buffer;
  vi.stubGlobal("crypto", {subtle: {digest: vi.fn().mockResolvedValue(digest)}, randomUUID: () => "idempotency-key"});
  request.mockImplementation(async path => path.endsWith("/content") ? {revision: 4, sha256: "ab".repeat(32)} : base);
  show();
  await screen.findByRole("button", {name: "Новая версия"});
  const selected = new File(["docx-content"], "different-name.docx");
  Object.defineProperty(selected, "arrayBuffer", {value: async () => new Uint8Array([1, 2, 3]).buffer});
  fireEvent.change(screen.getByLabelText("Новая версия оригинала letter.docx"), {target: {files: [selected]}});
  await screen.findByText("Редакция 4 сохранена в ACTI");
  expect(request).toHaveBeenCalledWith("/office-sync/files/logical-file/content", {
    method: "PUT", body: selected, headers: {"Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "X-CSRF-Token": "csrf", "If-Match": '"3"', "Idempotency-Key": "idempotency-key", "X-Content-SHA256": "ab".repeat(32)},
  });
});

it("does not claim success for a mismatching binary receipt", async () => {
  vi.stubGlobal("crypto", {subtle: {digest: vi.fn().mockResolvedValue(new Uint8Array(32).buffer)}, randomUUID: () => "idempotency-key"});
  request.mockImplementation(async path => path.endsWith("/content") ? {revision: 4, sha256: "different"} : base);
  show(); await screen.findByRole("button", {name: "Новая версия"});
  const selected = new File(["docx-content"], "letter.docx");
  Object.defineProperty(selected, "arrayBuffer", {value: async () => new ArrayBuffer(1)});
  fireEvent.change(screen.getByLabelText("Новая версия оригинала letter.docx"), {target: {files: [selected]}});
  expect((await screen.findByRole("alert")).textContent).toContain("Подтверждение содержимого не получено");
  expect(screen.queryByText("Редакция 4 сохранена в ACTI")).toBeNull();
});
