import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import type { ServiceAccount, ServiceScopeOptions, ServiceToken } from "../engine/serviceAccounts";
import { ServiceAccountsAdmin } from "./ServiceAccountsAdmin";
import { OperationsAdmin } from "./OperationsAdmin";
import { UsersAdmin } from "./AdminIdentity";

afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();});
const options: ServiceScopeOptions = {registries: [{id: "one", name: "Первый реестр"}, {id: "two", name: "Второй реестр"}], operations: [{id: "documents", label: "Документы"}, {id: "content", label: "Содержание"}]};
const account: ServiceAccount = {id: "service", user_id: "service-user", name: "Синтетическая интеграция", state: "active", registry_ids: ["one", "two"], operations: ["documents", "content"], created_at: "2026-01-01T00:00:00Z", tokens: []};
const token: ServiceToken = {id: "old", prefix: "public-prefix", expires_at: "2099-01-01T00:00:00Z", revoked_at: null, registry_ids: ["one"], operations: ["content"]};
const metrics = {content: {documents: 2, files: 1, extractions: {completed: 1}}, usage: [{action: "search", count: 3}], numbers: {visible_entries: 2}, execution: {visible_instances: 1}, queue: {pending: 1, failed: 1}, retrieval: {requests: 2, duration_ms_total: 30, time_to_open_count: 1, time_to_open_ms_total: 120}};
type Handler = (url: string, init?: RequestInit) => unknown;
function setup(element: React.ReactNode, handler: Handler = () => undefined) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data = handler(url, init);
    if (data instanceof Response) return data;
    if (url.endsWith("/users/me")) data = {id: "admin", display_name: "Администратор", roles: ["admin"], csrf_token: "csrf"};
    else if (data === undefined && url.endsWith("/service-accounts/options")) data = options;
    else if (data === undefined && url.includes("/service-accounts/service/tokens?") ) data = {items: [token], total: 1, page: 1, page_size: 20};
    else if (data === undefined && url.endsWith("/service-accounts/service")) data = account;
    else if (data === undefined && url.includes("/service-accounts?") ) data = {items: [account], total: 1, page: 1, page_size: 20};
    else if (data === undefined && url.includes("/operations/metrics")) data = metrics;
    else if (data === undefined && url.includes("/operations/queue?")) data = {items: [{id: "event", event_type: "document.updated", state: "failed", attempts: 5, created_at: "2026-01-01T00:00:00Z", error_code: "consumer_failed", payload: {secret: "DO-NOT-RENDER"}}], total: 1, page: 1, page_size: 20};
    return new Response(JSON.stringify(data === undefined ? {} : data));
  });
  vi.stubGlobal("fetch", fetch);
  function Ready() {return useAuth().user ? element : null;}
  render(<AuthProvider><Ready/></AuthProvider>);
  return fetch;
}
async function openService() {fireEvent.click(await screen.findByRole("button", {name: "Открыть сервис"})); await screen.findByRole("heading", {name: account.name});}

it("creates an explicit read-only service scope without a password or interactive roles", async () => {
  const fetch = setup(<ServiceAccountsAdmin/>, (url, init) => url.endsWith("/service-accounts") && init?.method === "POST" ? account : url.includes("/service-accounts?") ? {items: [], total: 21, page: 1, page_size: 20} : undefined);
  await screen.findByRole("option", {name: "Первый реестр"});
  fireEvent.click(screen.getByRole("button", {name: "Создать сервисную запись"}));
  const form = document.querySelector(".service-account-editor") as HTMLElement;
  fireEvent.change(within(form).getByLabelText("Название интеграции"), {target: {value: "Интеграция для чтения"}});
  fireEvent.click(within(form).getByLabelText("Первый реестр"));
  fireEvent.click(within(form).getByLabelText("Содержание и блоки"));
  fireEvent.click(within(form).getByRole("button", {name: "Сохранить сервисную запись"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/service-accounts") && init?.method === "POST")).toBe(true));
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/service-accounts") && init?.method === "POST")!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({name: "Интеграция для чтения", registry_ids: ["one"], operations: ["content"]});
  await screen.findByRole("heading", {name: account.name});
  expect(screen.queryByLabelText("Временный пароль")).toBeNull();
});

it("issues a narrower expiring token once and removes the secret on close without persisting it", async () => {
  const writes = vi.spyOn(Storage.prototype, "setItem");
  const fetch = setup(<ServiceAccountsAdmin/>, (url, init) => url.endsWith("/service/tokens") && init?.method === "POST" ? {id: "new", token: "synthetic-one-time-value", prefix: "new-prefix", expires_at: "2099-01-01T00:00:00Z"} : undefined);
  await openService();
  fireEvent.click(screen.getByRole("button", {name: "Создать токен"}));
  const form = document.querySelector(".token-action") as HTMLElement;
  fireEvent.change(within(form).getByLabelText(/^Срок токена, дней/), {target: {value: "7"}});
  fireEvent.click(within(form).getByLabelText("Второй реестр"));
  fireEvent.click(within(form).getByLabelText("Карточки и версии документов"));
  fireEvent.change(within(form).getByLabelText("Причина операции с токеном"), {target: {value: "Проверяемая интеграция"}});
  fireEvent.click(within(form).getByRole("button", {name: "Выпустить токен"}));
  const secret = await screen.findByLabelText("Одноразовый показ токена") as HTMLInputElement;
  expect(secret.type).toBe("password");
  expect(secret.value).toBe("synthetic-one-time-value");
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/service/tokens") && init?.method === "POST")!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({expires_in_days: 7, reason: "Проверяемая интеграция", registry_ids: ["one"], operations: ["content"]});
  fireEvent.click(screen.getByRole("button", {name: "Закрыть и скрыть токен"}));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByDisplayValue("synthetic-one-time-value")).toBeNull();
  expect(writes).not.toHaveBeenCalled();
  expect(window.location.href).not.toContain("synthetic-one-time-value");
});

it("requires a reason for token revocation and keeps the issued secret out of token history", async () => {
  const fetch = setup(<ServiceAccountsAdmin/>, (url, init) => url.endsWith("/old/revoke") && init?.method === "POST" ? new Response(null, {status: 204}) : undefined);
  await openService();
  fireEvent.click(await screen.findByRole("button", {name: "Отозвать токен"}));
  const confirm = screen.getByRole("button", {name: "Подтвердить отзыв токена"}) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Причина операции с токеном"), {target: {value: "Завершение интеграции"}});
  fireEvent.click(confirm);
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith("/old/revoke"))).toBe(true));
  const request = fetch.mock.calls.find(([url]) => url.endsWith("/old/revoke"))!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({reason: "Завершение интеграции"});
  expect(screen.queryByLabelText("Одноразовый показ токена")).toBeNull();
});

it("rotates a token with its prior subset rather than silently expanding to the account scope", async () => {
  const fetch = setup(<ServiceAccountsAdmin/>, (url, init) => url.endsWith("/old/rotate") && init?.method === "POST" ? {id: "new", token: "synthetic-rotated-value", prefix: "new", expires_at: "2099-01-01T00:00:00Z"} : undefined);
  await openService();
  fireEvent.click(await screen.findByRole("button", {name: "Ротировать токен"}));
  fireEvent.change(screen.getByLabelText("Причина операции с токеном"), {target: {value: "Плановая замена"}});
  fireEvent.click(screen.getByRole("button", {name: "Выпустить замену токена"}));
  await screen.findByRole("dialog");
  const request = fetch.mock.calls.find(([url]) => url.endsWith("/old/rotate"))!;
  expect(JSON.parse(String(request[1]?.body))).toMatchObject({registry_ids: ["one"], operations: ["content"], reason: "Плановая замена"});
  fireEvent.keyDown(window, {key: "Escape"});
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("shows service users without password-reset or role-elevation controls", async () => {
  setup(<UsersAdmin/>, url => url.endsWith("/admin/users") ? [{id: "service-user", login: "internal-service", display_name: "Интеграция API", active: true, roles: ["viewer"], must_change_password: false, kind: "service"}] : undefined);
  await screen.findByText("Сервис API · без пароля");
  expect(screen.queryByRole("button", {name: "Настроить"})).toBeNull();
  expect(screen.queryByRole("heading", {name: "Сброс пароля"})).toBeNull();
  expect(screen.getByText("Настройки в разделе API и сервисных учётных записей")).toBeTruthy();
});

it("shows aggregate diagnostics only and retries a failed event with an explicit reason", async () => {
  const fetch = setup(<OperationsAdmin/>);
  await screen.findByText("Среднее время: 15 мс");
  await screen.findByText("От поиска до открытия: 120 мс");
  expect(document.body.textContent).not.toContain("DO-NOT-RENDER");
  fireEvent.click(await screen.findByRole("button", {name: "Повторить обработку"}));
  const retry = screen.getByRole("button", {name: "Подтвердить повтор события"}) as HTMLButtonElement;
  expect(retry.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Причина повтора события"), {target: {value: "Исправлена причина ошибки"}});
  fireEvent.click(retry);
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith("/queue/event/retry"))).toBe(true));
  const request = fetch.mock.calls.find(([url]) => url.endsWith("/queue/event/retry"))!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({reason: "Исправлена причина ошибки"});
});

it("uses a generic empty access state without fabricated latency averages", async () => {
  setup(<OperationsAdmin/>, url => url.includes("/operations/metrics") ? {content: {documents: 0, files: 0, extractions: {}}, usage: [], numbers: {visible_entries: 0}, execution: {visible_instances: 0}, queue: {pending: 0, failed: 0}, retrieval: {requests: 0, duration_ms_total: 0}} : undefined);
  await screen.findByText("Нет доступных эксплуатационных данных.");
  expect(screen.getByText("Среднее время: Нет измерений")).toBeTruthy();
  expect(screen.getByText("От поиска до открытия: Нет измерений")).toBeTruthy();
});

it("copies only on explicit request and clears the one-time display after acknowledgement", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", {clipboard: {writeText}});
  setup(<ServiceAccountsAdmin/>, (url, init) => url.endsWith("/service/tokens") && init?.method === "POST" ? {id: "new", token: "synthetic-copy-value", prefix: "new", expires_at: "2099-01-01T00:00:00Z"} : undefined);
  await openService();
  fireEvent.click(screen.getByRole("button", {name: "Создать токен"}));
  fireEvent.change(screen.getByLabelText("Причина операции с токеном"), {target: {value: "Выдача интеграции"}});
  fireEvent.click(screen.getByRole("button", {name: "Выпустить токен"}));
  await screen.findByRole("dialog");
  expect(writeText).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", {name: "Скопировать токен"}));
  await screen.findByText("Токен скопирован");
  expect(writeText).toHaveBeenCalledWith("synthetic-copy-value");
  fireEvent.click(screen.getByRole("button", {name: "Закрыть и скрыть токен"}));
  expect(screen.queryByLabelText("Одноразовый показ токена")).toBeNull();
});

it("keeps earlier token pages reachable and disables issuance for a disabled service", async () => {
  const fetch = setup(<ServiceAccountsAdmin/>, url => {
    if (url.includes("/service-accounts?")) return {items: [{...account, state: "disabled"}], total: 1, page: 1, page_size: 20};
    if (url.endsWith("/service-accounts/service")) return {...account, state: "disabled"};
    if (url.includes("/service/tokens?")) return {items: [{...token, revoked_at: "2026-01-01T00:00:00Z"}], total: 21, page: 1, page_size: 20};
  });
  await openService();
  await screen.findByText("Отозван");
  expect(screen.queryByRole("button", {name: "Создать токен"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Ротировать токен"})).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "Следующие токены"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("/service/tokens?page=2"))).toBe(true));
});
