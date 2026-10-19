import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, apiRequest } from "../api/client";
import { OfficeSyncAgent, type AgentStatus } from "./OfficeSyncAgent";

vi.mock("../api/client", async original => ({...await original<typeof import("../api/client")>(), apiRequest: vi.fn()}));
const request = vi.mocked(apiRequest);
const empty = (): AgentStatus => ({online: false, last_seen_at: null, client_version: null, connections: [], documents: []});
const connected = (): AgentStatus => ({...empty(), online: true, last_seen_at: new Date().toISOString(), client_version: "1.1.0",
  connections: [{connection_id: "agent", online: true, last_seen_at: new Date().toISOString(), client_version: "1.1.0"}]});
const withDocument = (): AgentStatus => ({...connected(), documents: [{file_id: "original", current_file_id: "bytes-2", filename: "letter.docx", revision: 2,
  can_edit: false, can_control: true, reason: "Оригинал уже открыт", session_id: "session", last_seen_at: new Date().toISOString(),
  last_saved_at: "2026-10-19T09:00:00Z", state: "saved", lease_expires_at: new Date(Date.now() + 60000).toISOString(), editor: null,
  document_id: "document", document_title: "Письмо", document_open: false, opened_at: "2026-10-19T08:00:00Z", closed_at: "2026-10-19T08:30:00Z", connection_id: "agent", command: null}]});
const show = (canControl = true) => render(<><input aria-label="Черновик" defaultValue=""/><OfficeSyncAgent owner="user" csrfToken="csrf" canControl={canControl}/></>);
const openPanel = () => fireEvent.click(screen.getByRole("button", {name: /ACTI Sync:/}));

it("automatically attaches an offline browser through the discovered local bridge without a connect button or protocol prompt", async () => {
  const nonce = "u".repeat(43);
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith("/v1/connect")) return new Response('{"ok":true}');
    if (url === "http://127.0.0.1:43130/v1/status") return new Response(JSON.stringify({version: "1.3.0", challenge: nonce}));
    throw new TypeError("unavailable");
  }); vi.stubGlobal("fetch", fetcher);
  request.mockImplementation(async path => path.endsWith("/connect") ? {uri: "acti-sync://connect?ticket=" + "a".repeat(43), expires_at: "2099-01-01"} : empty());
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  show();
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:43130/v1/connect", expect.objectContaining({credentials: "omit", body: JSON.stringify({ticket: "a".repeat(43), challenge: nonce})})));
  expect(request.mock.calls.filter(([path]) => path.endsWith("/connect"))).toHaveLength(1);
  expect(request).toHaveBeenCalledWith("/office-sync/agent/connect", expect.objectContaining({method: "POST", headers: {"X-CSRF-Token": "csrf"}}));
  openPanel(); expect(screen.queryByRole("button", {name: "Подключить агент"})).toBeNull();
  expect(screen.queryByRole("button", {name: "ACTI Sync: на связи"})).toBeNull(); expect(click).not.toHaveBeenCalled();
});

it("does not issue connect tickets for an absent agent, an online browser, or a reader", async () => {
  const view = show(); await act(async () => {});
  expect(request.mock.calls.some(([path]) => path.endsWith("/connect"))).toBe(false); view.unmount();
  request.mockResolvedValue(connected()); const online = show(); await act(async () => {}); expect(fetch).toHaveBeenCalledTimes(16); online.unmount();
  request.mockResolvedValue(empty()); show(false); await act(async () => {}); expect(fetch).toHaveBeenCalledTimes(16);
});

it("cancels local discovery and a late connect ticket on browser session expiry", async () => {
  let resolve!: (value: unknown) => void;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "http://127.0.0.1:43127/v1/status") return new Response(JSON.stringify({version: "1.3.0", challenge: "x".repeat(43)}));
    throw new TypeError("unavailable");
  }));
  request.mockImplementation(async path => path.endsWith("/connect") ? new Promise(done => {resolve = done;}) : empty());
  show(); await waitFor(() => expect(resolve).toBeTypeOf("function"));
  const signal = request.mock.calls.find(([path]) => path.endsWith("/connect"))?.[1]?.signal;
  act(() => {window.dispatchEvent(new Event("acti:unauthorized"));}); expect(signal?.aborted).toBe(true);
  await act(async () => {resolve({uri: "acti-sync://connect?ticket=" + "b".repeat(43), expires_at: "2099-01-01"});});
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/v1/connect"))).toBe(false);
});

it("opens the existing global panel from the tray query", async () => {
  window.history.replaceState({}, "", "/documents?sync=1"); show();
  expect(await screen.findByRole("dialog", {name: "ACTI Sync"})).toBeTruthy();
});

it("bounds discovery retries and never loops connect tickets while the heartbeat remains offline", async () => {
  vi.useFakeTimers(); show(); await act(async () => {});
  await act(async () => {await vi.advanceTimersByTimeAsync(120000);});
  expect(fetch).toHaveBeenCalledTimes(48);
  expect(request.mock.calls.some(([path]) => path.endsWith("/connect"))).toBe(false);
});

it("does not issue another connect ticket after an ambiguous POST or a transient server status error", async () => {
  vi.useFakeTimers(); let unavailable = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "http://127.0.0.1:43127/v1/status") return new Response(JSON.stringify({version: "1.3.0", challenge: "q".repeat(43)}));
    throw new TypeError("unavailable");
  }));
  request.mockImplementation(async path => {
    if (path.endsWith("/connect")) return {uri: "acti-sync://connect?ticket=" + "e".repeat(43), expires_at: "2099-01-01"};
    if (unavailable) throw new Error("network");
    return empty();
  });
  show(); await act(async () => {});
  expect(request.mock.calls.filter(([path]) => path.endsWith("/connect"))).toHaveLength(1);
  unavailable = true; await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  unavailable = false; await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(request.mock.calls.filter(([path]) => path.endsWith("/connect"))).toHaveLength(1);
});
beforeEach(() => {request.mockReset(); request.mockImplementation(async () => empty()); vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("unavailable")));});
afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); window.history.replaceState({}, "", "/");});

it("shows agent presence without documents, and keeps the browser form when the panel opens and closes", async () => {
  request.mockResolvedValue(connected()); show();
  await screen.findByRole("button", {name: "ACTI Sync: на связи"});
  fireEvent.change(screen.getByLabelText("Черновик"), {target: {value: "Несохранённое"}});
  openPanel();
  const panel = screen.getByRole("dialog", {name: "ACTI Sync"});
  expect(within(panel).getByText("Версия агента: 1.1.0")).toBeTruthy();
  expect(within(panel).getByText("Открытых сеансов редактирования нет.")).toBeTruthy();
  fireEvent.click(within(panel).getByRole("button", {name: "Закрыть"}));
  expect((screen.getByLabelText("Черновик") as HTMLInputElement).value).toBe("Несохранённое");
});

it("separates closed Word, server ACK and presence; commands do not depend on the launch lease capability", async () => {
  request.mockImplementation(async path => path.endsWith("/commands") ? {id: "command", state: "pending"} : withDocument());
  show(); await screen.findByRole("button", {name: "ACTI Sync: на связи"}); openPanel();
  expect(screen.getByText("Письмо")).toBeTruthy(); expect(screen.getByText("letter.docx")).toBeTruthy();
  expect(screen.getByText("Закрыт в Word")).toBeTruthy();
  expect(screen.getByText(/Последняя серверная синхронизация:/)).toBeTruthy();
  expect(screen.getByText(/Закрыт:/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Повторить отправку"}));
  await screen.findByText("Повторить отправку: ожидает выполнения");
  expect(request).toHaveBeenCalledWith("/office-sync/agent/commands", expect.objectContaining({method: "POST", body: JSON.stringify({session_id: "session", action: "retry"}), headers: {"X-CSRF-Token": "csrf"}}));
});

it("shows command result states but never displays a local path or credentials from command errors", async () => {
  const data = withDocument(); data.documents[0].command = {id: "command", action: "retry", state: "failed", error: "C:\\Users\\private\\token-value"};
  request.mockResolvedValue(data); show(); await screen.findByRole("button", {name: "ACTI Sync: на связи"}); openPanel();
  expect(screen.getByText("Повторить отправку: ошибка выполнения")).toBeTruthy();
  expect(document.body.textContent).not.toContain("private"); expect(document.body.textContent).not.toContain("token-value");
});

it("readers and records without write permission see status without command controls", async () => {
  request.mockResolvedValue(withDocument()); const view = show(false);
  await screen.findByRole("button", {name: "ACTI Sync: на связи"}); openPanel();
  expect(screen.getByText("letter.docx")).toBeTruthy();
  expect(screen.queryByRole("button", {name: "Подключить агент"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Повторить отправку"})).toBeNull();
  view.unmount(); const data = withDocument(); data.documents[0].can_control = false; request.mockResolvedValue(data); show();
  await screen.findByRole("button", {name: "ACTI Sync: на связи"}); openPanel();
  expect(screen.queryByRole("button", {name: "Повторить отправку"})).toBeNull();
});

it("polls every seven seconds and expires a stale heartbeat even while the next poll hangs", async () => {
  vi.useFakeTimers(); request.mockResolvedValue(connected()); show(); await act(async () => {});
  expect(screen.getByRole("button", {name: "ACTI Sync: на связи"})).toBeTruthy();
  request.mockImplementation(() => new Promise(() => {}));
  await act(async () => {await vi.advanceTimersByTimeAsync(36000);});
  expect(request).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("button", {name: "ACTI Sync: нет связи"})).toBeTruthy();
});

it("clears documents on auth failure, stops polling and aborts outstanding requests on unmount", async () => {
  vi.useFakeTimers(); request.mockResolvedValue(withDocument()); const view = show(); await act(async () => {}); openPanel();
  request.mockRejectedValue(new ApiError(403, "forbidden", "denied"));
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(screen.queryByText("letter.docx")).toBeNull();
  await act(async () => {await vi.advanceTimersByTimeAsync(21000);}); expect(request).toHaveBeenCalledTimes(2);
  view.unmount(); request.mockImplementation(() => new Promise(() => {})); const second = show();
  const signal = request.mock.calls.at(-1)?.[1]?.signal; second.unmount(); expect(signal?.aborted).toBe(true);
});

it("updates pending commands to server result, and allows each supported action without navigation", async () => {
  vi.useFakeTimers(); let data = withDocument();
  request.mockImplementation(async (path, init) => {
    if (path.endsWith("/commands")) {
      const {action} = JSON.parse(init?.body as string);
      data = {...data, documents: data.documents.map(item => ({...item, command: {id: action, action, state: "pending"}}))};
      return {id: action, state: "pending"};
    }
    return data;
  });
  show(); await act(async () => {}); openPanel();
  for (const [action, label, result] of [["open", "Открыть в Word", "succeeded"], ["retry", "Повторить отправку", "expired"], ["finish", "Завершить сеанс", "succeeded"], ["reveal", "Открыть папку", "succeeded"]] as const) {
    await act(async () => {fireEvent.click(screen.getByRole("button", {name: label}));});
    expect(screen.getByText(`${label}: ожидает выполнения`)).toBeTruthy();
    data = {...data, documents: data.documents.map(item => ({...item, command: {id: action, action, state: result}}))};
    await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
    expect(screen.getByText(`${label}: ${result === "expired" ? "время ожидания истекло" : "выполнено"}`)).toBeTruthy();
  }
  expect(screen.getByRole("dialog", {name: "ACTI Sync"})).toBeTruthy();
});

it("shows historical Word state and ACK time without a current saved or online claim after heartbeat expiry", async () => {
  vi.useFakeTimers(); request.mockResolvedValue(withDocument()); show(); await act(async () => {}); openPanel();
  expect(screen.getByText("Сохранено в ACTI")).toBeTruthy();
  request.mockImplementation(() => new Promise(() => {}));
  await act(async () => {await vi.advanceTimersByTimeAsync(36000);});
  expect(screen.queryByText("Сохранено в ACTI")).toBeNull();
  expect(screen.getByText("Последнее известное состояние: закрыт в Word")).toBeTruthy();
  expect(screen.getByText(/Последняя серверная синхронизация:/)).toBeTruthy();
  expect((screen.getByRole("button", {name: "Повторить отправку"}) as HTMLButtonElement).disabled).toBe(true);
});

it("resets and aborts polling when the browser identity changes", async () => {
  vi.useFakeTimers(); request.mockResolvedValue(withDocument());
  const view = render(<OfficeSyncAgent owner="first" csrfToken="csrf-1" canControl/>); await act(async () => {}); openPanel();
  request.mockImplementation(() => new Promise(() => {}));
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  const previous = request.mock.calls.at(-1)?.[1]?.signal;
  view.rerender(<OfficeSyncAgent owner="second" csrfToken="csrf-2" canControl/>);
  expect(previous?.aborted).toBe(true);
  expect(screen.queryByRole("dialog", {name: "ACTI Sync"})).toBeNull();
  openPanel(); expect(screen.queryByText("letter.docx")).toBeNull();
});

it("keeps the document workspace usable when a status response is malformed", async () => {
  request.mockResolvedValue({}); show();
  await screen.findByRole("button", {name: "ACTI Sync: нет связи"}); openPanel();
  expect(screen.getByText("Нет связи с ACTI. Состояние агента неизвестно.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Закрыть"}));
  expect(screen.getByLabelText("Черновик")).toBeTruthy();
});

it("shows an editing pencil only for a fresh open document and removes it on close or heartbeat expiry", async () => {
  vi.useFakeTimers(); let data = withDocument(); data.documents[0].document_open = true;
  request.mockImplementation(async () => data); show(); await act(async () => {});
  expect(screen.getByRole("button", {name: "ACTI Sync: на связи · редактируется документов: 1"})).toBeTruthy();
  expect(screen.getByTitle("Редактируется документов: 1")).toBeTruthy();
  data = {...data, documents: data.documents.map(item => ({...item, document_open: false}))};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(screen.queryByTitle("Редактируется документов: 1")).toBeNull();
  data = {...data, documents: data.documents.map(item => ({...item, document_open: true}))};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(screen.getByTitle("Редактируется документов: 1")).toBeTruthy();
  request.mockImplementation(() => new Promise(() => {}));
  await act(async () => {await vi.advanceTimersByTimeAsync(36000);});
  expect(screen.queryByTitle("Редактируется документов: 1")).toBeNull();
});

it("notifies real open, local save, server ACK and close transitions without announcing initial or repeated history", async () => {
  vi.useFakeTimers(); let data = withDocument(); request.mockImplementation(async () => data);
  show(); await act(async () => {});
  expect(screen.queryByRole("region", {name: "Уведомления ACTI Sync"})).toBeNull();
  data = {...data, documents: data.documents.map(item => ({...item, document_open: true, state: "editing"}))};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  let notices = screen.getByRole("region", {name: "Уведомления ACTI Sync"});
  expect(within(notices).getByText("Письмо: открыт в Word")).toBeTruthy();
  expect(within(notices).queryByText(/сохранено в ACTI/)).toBeNull();
  data = {...data, documents: data.documents.map(item => ({...item, state: "local_saved"}))};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  notices = screen.getByRole("region", {name: "Уведомления ACTI Sync"});
  expect(within(notices).getByText("Письмо: сохранено локально, ожидается подтверждение ACTI")).toBeTruthy();
  data = {...data, documents: data.documents.map(item => ({...item, state: "saved", revision: 3, last_saved_at: new Date().toISOString()}))};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  notices = screen.getByRole("region", {name: "Уведомления ACTI Sync"});
  expect(within(notices).getByText("Письмо: редакция 3 сохранена в ACTI")).toBeTruthy();
  data = {...data, documents: data.documents.map(item => ({...item, document_open: false}))};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  notices = screen.getByRole("region", {name: "Уведомления ACTI Sync"});
  expect(within(notices).getByText("Письмо: закрыт в Word")).toBeTruthy();
  await act(async () => {await vi.advanceTimersByTimeAsync(14000);});
  expect(screen.queryByRole("region", {name: "Уведомления ACTI Sync"})).toBeNull();
});

it("does not turn a completed open command or missing ACK into a Word-open or saved notification", async () => {
  vi.useFakeTimers(); let data = withDocument(); request.mockImplementation(async () => data);
  show(); await act(async () => {});
  data = {...data, documents: data.documents.map(item => ({...item, state: "saved", last_saved_at: null,
    command: {id: "open-command", action: "open", state: "succeeded"}}))};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(screen.queryByRole("region", {name: "Уведомления ACTI Sync"})).toBeNull();
  expect(screen.queryByTitle("Редактируется документов: 1")).toBeNull();
});

it("ignores unbound documents and clears active-edit notices immediately when browser authentication expires", async () => {
  vi.useFakeTimers(); let data = withDocument(); data.documents[0].document_open = true; data.documents[0].connection_id = "another-browser";
  request.mockImplementation(async () => data); show(); await act(async () => {});
  expect(screen.queryByTitle("Редактируется документов: 1")).toBeNull();
  data = {...data, documents: data.documents.map(item => ({...item, connection_id: "agent", opened_at: new Date(Date.now() + 1000).toISOString()}))};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(screen.getByRole("region", {name: "Уведомления ACTI Sync"})).toBeTruthy();
  expect(screen.getByTitle("Редактируется документов: 1")).toBeTruthy();
  act(() => {window.dispatchEvent(new Event("acti:unauthorized"));});
  expect(screen.queryByRole("region", {name: "Уведомления ACTI Sync"})).toBeNull();
  expect(screen.queryByTitle("Редактируется документов: 1")).toBeNull();
});

it("announces a new session opened, saved and closed entirely between successful status polls", async () => {
  vi.useFakeTimers(); const baseline = Date.now(); let data = connected();
  request.mockImplementation(async () => data); show(); await act(async () => {});
  data = withDocument(); data.documents[0] = {...data.documents[0], document_open: false,
    opened_at: new Date(baseline + 1000).toISOString(), last_saved_at: new Date(baseline + 2000).toISOString(),
    closed_at: new Date(baseline + 3000).toISOString()};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  const notices = screen.getByRole("region", {name: "Уведомления ACTI Sync"});
  expect(within(notices).getByText("Письмо: открыт в Word")).toBeTruthy();
  expect(within(notices).getByText("Письмо: закрыт в Word")).toBeTruthy();
  expect(within(notices).getByText("Письмо: редакция 2 сохранена в ACTI")).toBeTruthy();
  expect(screen.queryByTitle("Редактируется документов: 1")).toBeNull();
});

it("detects changed open and close timestamps even when a known session remains closed between polls", async () => {
  vi.useFakeTimers(); const baseline = Date.now(); let data = withDocument();
  request.mockImplementation(async () => data); show(); await act(async () => {});
  data = {...data, documents: data.documents.map(item => ({...item, opened_at: new Date(baseline + 1000).toISOString(), closed_at: new Date(baseline + 2000).toISOString()}))};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  const notices = screen.getByRole("region", {name: "Уведомления ACTI Sync"});
  expect(within(notices).getAllByText("Письмо: открыт в Word")).toHaveLength(1);
  expect(within(notices).getAllByText("Письмо: закрыт в Word")).toHaveLength(1);
  expect(within(notices).queryByText(/сохранена в ACTI/)).toBeNull();
});

it("does not announce history older than the previous successful poll for a newly visible session", async () => {
  vi.useFakeTimers(); const baseline = Date.now(); let data = connected();
  request.mockImplementation(async () => data); show(); await act(async () => {});
  data = withDocument(); data.documents[0] = {...data.documents[0], document_open: true,
    opened_at: new Date(baseline - 1000).toISOString(), closed_at: new Date(baseline - 500).toISOString(),
    last_saved_at: new Date(baseline - 1000).toISOString()};
  await act(async () => {await vi.advanceTimersByTimeAsync(7000);});
  expect(screen.queryByRole("region", {name: "Уведомления ACTI Sync"})).toBeNull();
});
