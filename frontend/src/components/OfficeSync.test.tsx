import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FileActions, FileRevisionHistory, validSyncUri, type OfficeSyncStatus } from "./OfficeSync";
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
beforeEach(() => {request.mockReset(); vi.mocked(saveDownload).mockReset(); request.mockResolvedValue(base); vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("unavailable")));});
afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();});
const show = (mode: "editor" | "viewer" = "editor", sync = true) => render(<FileActions file={file} mode={mode} csrfToken="csrf" sync={sync}/>);

it("shows only two compact actions with no guides, manual version upload or viewing applications", async () => {
  show(); await screen.findByRole("button", {name: "Редактировать в Word"});
  expect(screen.getAllByRole("button").map(button => button.textContent)).toEqual(["Редактировать в Word", "Скачать"]);
  expect(document.querySelector("details")).toBeNull();
  expect(document.querySelector('input[type="file"]')).toBeNull();
  expect(screen.queryByRole("status")).toBeNull();
});

it("requires server capability, and readers never get a write action", async () => {
  request.mockResolvedValue({...base, can_edit: false, reason: "Нет права загрузки"});
  const {unmount} = show();
  await act(async () => {});
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
  unmount();
  request.mockResolvedValue(base);
  show("viewer");
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.stringContaining("mode=viewer"), expect.anything()));
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Открыть в Word (просмотр)"})).toBeNull();
});

it("launches only an exact one-use-ticket protocol, preserves CSRF, and does not invent editing or ACK", async () => {
  request.mockImplementation(async path => path === "/office-sync/launch" ? {uri: "acti-sync://launch?ticket=" + "a".repeat(43), session_id: "session", expires_at: "2099-01-01"} : base);
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  show();
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать в Word"}));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(request).toHaveBeenCalledWith("/office-sync/launch", expect.objectContaining({method: "POST", headers: {"X-CSRF-Token": "csrf"}, body: JSON.stringify({file_id: file.id})}));
  expect(screen.queryByText("Редактируется")).toBeNull();
  expect(screen.queryByText("Сохранено в ACTI")).toBeNull();
  expect(document.querySelector("details")).toBeNull();
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
  await screen.findByRole("alert");
  expect(click).not.toHaveBeenCalled();
  expect(validSyncUri("acti-sync://launch?ticket=" + "a".repeat(43) + "&origin=https://example.com")).toBe(false);
});

it("uses the discovered bridge and never replays an ambiguously accepted launch through the protocol", async () => {
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith("/v1/launch")) throw new TypeError("lost after acceptance");
    if (url === "http://127.0.0.1:43127/v1/status") return new Response(JSON.stringify({version: "1.3.0", challenge: "z".repeat(43)}));
    throw new TypeError("unavailable");
  }); vi.stubGlobal("fetch", fetcher);
  request.mockImplementation(async path => path === "/office-sync/launch" ? {uri: "acti-sync://launch?ticket=" + "b".repeat(43), expires_at: "2099-01-01"} : base);
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {}); show();
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать в Word"})); await screen.findByRole("alert");
  expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:43127/v1/launch", expect.objectContaining({credentials: "omit", body: JSON.stringify({ticket: "b".repeat(43), challenge: "z".repeat(43)})}));
  expect(click).not.toHaveBeenCalled();
});

it("drops and aborts a pending launch after switching files", async () => {
  let resolve!: (value: unknown) => void;
  request.mockImplementation(async path => path === "/office-sync/launch" ? new Promise(done => {resolve = done;}) : base);
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {}); const view = show();
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать в Word"})); await waitFor(() => expect(resolve).toBeTypeOf("function"));
  const signal = request.mock.calls.find(([path]) => path === "/office-sync/launch")?.[1]?.signal;
  view.rerender(<FileActions file={{...file, id: "another"}} mode="editor" csrfToken="csrf"/>); expect(signal?.aborted).toBe(true);
  await act(async () => {resolve({uri: "acti-sync://launch?ticket=" + "d".repeat(43), expires_at: "2099-01-01"});}); expect(click).not.toHaveBeenCalled();
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
  render(<><FileActions file={file} mode="editor" csrfToken="csrf"/><FileRevisionHistory file={file} mode="editor"/></>);
  await screen.findByRole("button", {name: "Редактировать в Word"});
  fireEvent.click(screen.getByRole("button", {name: "Скачать"}));
  expect(saveDownload).toHaveBeenCalledWith("bytes-3", "letter.docx", "editor");
  const history = screen.getByText("letter.docx").closest("details")!;
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
  expect(screen.queryByText("Конфликт: требуется действие")).toBeNull();
  expect((screen.getByRole("textbox", {name: "Название"}) as HTMLInputElement).value).toBe("Несохранённый черновик");
});

it("never launches or polls historical metadata revisions", () => {
  show("editor", false);
  expect(request).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
});

it("offers legacy DOC editing through the stable original with CSRF and preserves each history filename", async () => {
  const legacy = {...file, name: "published-original.DOC", mime: "application/msword"};
  request.mockImplementation(async path => {
    if (path === "/office-sync/launch") return {uri: "acti-sync://launch?ticket=" + "c".repeat(43), session_id: "legacy-session", expires_at: "2099-01-01"};
    if (path.includes("/history")) return {items: [
      {file_id: "immutable-doc", filename: "published-original.DOC", revision: 1, size: 1024, created_at: "2026-01-01"},
      {file_id: "immutable-docx", filename: "published-original.docx", revision: 2, size: 2048, created_at: "2026-01-02"},
    ]};
    return {...base, filename: "published-original.docx", current_file_id: "immutable-docx", revision: 2};
  });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<><FileActions file={legacy} mode="editor" csrfToken="legacy-csrf"/><FileRevisionHistory file={legacy} mode="editor"/></>);
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать в Word"}));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(request).toHaveBeenCalledWith("/office-sync/launch", expect.objectContaining({method: "POST", headers: {"X-CSRF-Token": "legacy-csrf"}, body: JSON.stringify({file_id: "logical-file"})}));
  fireEvent.click(screen.getByRole("button", {name: "Скачать"}));
  expect(saveDownload).toHaveBeenLastCalledWith("immutable-docx", "published-original.docx", "editor");
  const history = screen.getByText("published-original.DOC").closest("details")!;
  history.open = true; fireEvent(history, new Event("toggle"));
  fireEvent.click(await screen.findByRole("button", {name: "Скачать редакцию 1"}));
  expect(saveDownload).toHaveBeenLastCalledWith("immutable-doc", "published-original.DOC", "editor");
  fireEvent.click(screen.getByRole("button", {name: "Скачать редакцию 2"}));
  expect(saveDownload).toHaveBeenLastCalledWith("immutable-docx", "published-original.docx", "editor");
});

it("keeps server-denied legacy DOC files and readers without editing actions", async () => {
  request.mockResolvedValue({...base, filename: "legacy.doc", can_edit: false, reason: "Нет права загрузки"});
  const legacy = {...file, name: "legacy.doc", mime: "application/msword"};
  const view = render(<FileActions file={legacy} mode="editor" csrfToken="csrf"/>);
  await act(async () => {});
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Новая версия"})).toBeNull();
  view.unmount(); request.mockResolvedValue({...base, filename: "legacy.doc"});
  render(<FileActions file={legacy} mode="viewer" csrfToken="csrf"/>);
  await waitFor(() => expect(request).toHaveBeenCalledWith("/office-sync/files/logical-file/status?mode=viewer", expect.anything()));
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Новая версия"})).toBeNull();
});

it("does not offer DOC conversion or editing for PDF files", () => {
  render(<FileActions file={{...file, name: "scan.pdf", mime: "application/pdf"}} mode="editor" csrfToken="csrf"/>);
  expect(request).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Новая версия"})).toBeNull();
});

it("shows legacy DOC editing in the dynamic original field without losing form changes", async () => {
  const legacy = {...file, name: "legacy.doc", mime: "application/msword"};
  function Form() {
    const [values, setValues] = useState({title: "Исходное", original: [legacy.id]});
    return <DynamicForm schema={{sections: [{key: "main", title: "Карточка", fields: [{key: "title", label: "Название", type: "text", editable: true}, {key: "original", label: "Оригинал", type: "file", editable: true}]}]}}
      values={values} onChange={next => setValues(next as typeof values)} mode="edit" roles={["editor"]} files={[legacy]} officeSync={{csrfToken: "csrf"}}/>;
  }
  render(<Form/>);
  fireEvent.change(screen.getByRole("textbox", {name: "Название"}), {target: {value: "Несохранённая правка"}});
  await screen.findByRole("button", {name: "Редактировать в Word"});
  expect((screen.getByRole("textbox", {name: "Название"}) as HTMLInputElement).value).toBe("Несохранённая правка");
});

it.each(["doc", "docx", "docm", "dot", "dotx", "dotm", "rtf", "odt"])("offers editing of supported %s source documents", async extension => {
  render(<FileActions file={{...file, name: `original.${extension}`}} mode="editor" csrfToken="csrf"/>);
  await screen.findByRole("button", {name: "Редактировать в Word"});
});

it("keeps an own active session reopen action after saving without starting another launch", async () => {
  const now = new Date().toISOString();
  request.mockImplementation(async path => path.endsWith("/commands") ? {id: "open-command", state: "pending"} : {...base,
    can_edit: false, can_reopen: true, state: "saved", session_id: "own-session", document_open: false,
    last_seen_at: now, last_saved_at: now, lease_expires_at: new Date(Date.now() + 60000).toISOString()});
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  show();
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать в Word"}));
  await waitFor(() => expect(request.mock.calls.some(([path]) => path.endsWith("/commands"))).toBe(true));
  expect(request).toHaveBeenCalledWith("/office-sync/agent/commands", expect.objectContaining({method: "POST", headers: {"X-CSRF-Token": "csrf"}, body: JSON.stringify({session_id: "own-session", action: "open"})}));
  expect(request.mock.calls.some(([path]) => path === "/office-sync/launch")).toBe(false);
  expect(click).not.toHaveBeenCalled();
  expect(screen.queryByText("Открыт в Word")).toBeNull();
});

it("never reopens another browser's lease, a stale own session, or a viewer session", async () => {
  request.mockResolvedValue({...base, can_edit: false, can_reopen: false, state: "saved", session_id: "other-session", last_seen_at: new Date().toISOString(), lease_expires_at: new Date(Date.now() + 60000).toISOString()});
  const first = show(); await act(async () => {});
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull(); first.unmount();
  request.mockResolvedValue({...base, can_edit: false, can_reopen: true, session_id: "stale", last_seen_at: "2020-01-01", lease_expires_at: "2020-01-02"});
  const second = show(); await act(async () => {});
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull(); second.unmount();
  request.mockResolvedValue({...base, can_edit: false, can_reopen: true, session_id: "own", last_seen_at: new Date().toISOString(), lease_expires_at: new Date(Date.now() + 60000).toISOString()});
  show("viewer"); await act(async () => {});
  expect(screen.queryByRole("button", {name: "Редактировать в Word"})).toBeNull();
});
