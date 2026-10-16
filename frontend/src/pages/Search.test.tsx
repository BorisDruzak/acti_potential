import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { Documents } from "./Documents";

afterEach(() => {cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/");});

it("uses one search field and retains the list, query and URL when opening a result", async () => {
  const card = {id: "document", registry_id: "registry", document_type_id: "type", schema_version_id: "schema",
    revision: 1, current_version: 1, title: "Озеленение", values: {}, display_values: {},
    schema: {sections: []}, permissions: ["read"], files: []};
  const requests: {url: string; body?: unknown}[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({url, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined});
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "user", display_name: "User", roles: ["viewer"], csrf_token: "csrf"};
    else if (url.endsWith("/registries")) data = [{id: "registry", name: "Реестр", permissions: ["read"], document_type_ids: ["type"]}];
    else if (url.endsWith("/search")) data = {items: [{document: card, matches: [], score: .6, match_count: 0}], total: 1, next_cursor: null};
    else if (url.includes("/documents/document?")) data = card;
    return new Response(JSON.stringify(data));
  }));
  function Ready() {return useAuth().user ? <Documents readOnly /> : null;}
  render(<AuthProvider><Ready /></AuthProvider>);
  fireEvent.change(await screen.findByLabelText("Поиск"), {target: {value: "озеленение"}});
  expect(screen.getAllByRole("searchbox")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(requests.some(r => r.url.endsWith("/search") && (r.body as {query?: string})?.query === "озеленение")).toBe(true));
  fireEvent.click(await screen.findByRole("button", {name: "Открыть карточку"}));
  await screen.findByRole("button", {name: "Содержание"});
  expect(screen.getByRole("button", {name: "Открыть карточку"})).toBeTruthy();
  expect((screen.getByLabelText("Поиск") as HTMLInputElement).value).toBe("озеленение");
  expect(new URLSearchParams(window.location.search).get("q")).toBe("озеленение");
  expect(new URLSearchParams(window.location.search).get("document")).toBe("document");
});

it("shows a running search clock, freezes its duration and resets on a repeated query", async () => {
  let now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  const pending: ((response: Response) => void)[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/search") && JSON.parse(String(init?.body)).query === "таймер") {
      return new Promise<Response>(resolve => pending.push(resolve));
    }
    const data = url.endsWith("/users/me") ? {id: "user", roles: ["viewer"], csrf_token: "csrf"} : url.endsWith("/registries") ? [{id: "registry", name: "Реестр", permissions: ["read"]}] : url.endsWith("/search") ? {items: [], total: 0} : [];
    return new Response(JSON.stringify(data));
  }));
  function Ready() {return useAuth().user ? <Documents readOnly /> : null;}
  render(<AuthProvider><Ready /></AuthProvider>);
  fireEvent.change(await screen.findByLabelText("Поиск"), {target: {value: "таймер"}});
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(pending).toHaveLength(1));
  expect(screen.getByText("Идёт поиск…")).toBeTruthy();
  now = 2250;
  await screen.findByText("1,25 с");
  pending[0](new Response(JSON.stringify({items: [], total: 0})));
  await screen.findByText("Время поиска: 1,25 с");
  now = 9000;
  expect(screen.getByText("Время поиска: 1,25 с")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(pending).toHaveLength(2));
  expect(screen.getByText("Идёт поиск…")).toBeTruthy();
  expect(screen.getByText("0,00 с")).toBeTruthy();
  now = 9250;
  pending[1](new Response(JSON.stringify({error: {code: "unavailable", message: "Поиск недоступен"}}), {status: 503}));
  await screen.findByText("Поиск не выполнен · 0,25 с");
  expect(screen.getByRole("alert").textContent).toBe("Поиск недоступен");
});

it("submits the general query, registration number and registration dates together", async () => {
  const requests: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/search")) requests.push(JSON.parse(String(init?.body)));
    const data = url.endsWith("/users/me") ? {id: "user", roles: ["viewer"], csrf_token: "csrf"} : url.endsWith("/registries") ? [{id: "registry", name: "Реестр", permissions: ["read"]}] : url.endsWith("/search") ? {items: [], total: 0} : [];
    return new Response(JSON.stringify(data));
  }));
  function Ready() {return useAuth().user ? <Documents readOnly /> : null;}
  render(<AuthProvider><Ready /></AuthProvider>);
  fireEvent.change(await screen.findByLabelText("Поиск"), {target: {value: "озеленение"}});
  fireEvent.change(screen.getByLabelText("Регистрационный номер"), {target: {value: "42-П"}});
  fireEvent.change(screen.getByLabelText("Дата регистрации", {exact: true}), {target: {value: "2026-10-16"}});
  expect((screen.getByLabelText("Дата регистрации с") as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(requests).toContainEqual(expect.objectContaining({query: "озеленение", reg_number: "42-П", date_from: "2026-10-16", date_to: "2026-10-16"})));
  expect(new URLSearchParams(window.location.search).get("reg_date")).toBe("2026-10-16");
});

it("refreshes retained search results after a saved revision is indexed", async () => {
  let saved = false, indexed = false;
  const card = () => ({id: "document", registry_id: "registry", document_type_id: "type", schema_version_id: "schema", revision: saved ? 2 : 1, current_version: 1, title: saved ? "Обновлено" : "Исходное", values: {}, display_values: {}, schema: {sections: []}, permissions: ["read", "update"], files: []});
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "user", roles: ["editor"], csrf_token: "csrf"};
    else if (url.endsWith("/registries")) data = [{id: "registry", name: "Реестр", permissions: ["read"]}];
    else if (url.endsWith("/search-index")) {indexed = true; data = {ready: true};}
    else if (url.endsWith("/search")) data = {items: [{document: {...card(), title: indexed ? "Обновлено" : "Исходное"}, matches: [], score: 0, match_count: 0}], total: 1};
    else if (url.includes("/documents/document")) {if (init?.method === "PUT") saved = true; data = card();}
    return new Response(JSON.stringify(data));
  }));
  function Ready() {return useAuth().user ? <Documents /> : null;}
  render(<AuthProvider><Ready /></AuthProvider>);
  fireEvent.click(await screen.findByRole("button", {name: "Открыть карточку"}));
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  fireEvent.click(screen.getByRole("button", {name: "Сохранить карточку"}));
  await waitFor(() => expect(indexed).toBe(true));
  await waitFor(() => expect(screen.getAllByText("Обновлено").length).toBeGreaterThan(0));
  expect(screen.getByRole("button", {name: "Открыть карточку"})).toBeTruthy();
});

it("restores another file in the same document on browser history navigation", async () => {
  window.history.replaceState(null, "", "/view?document=document&tab=content&file=first");
  const card = {id: "document", revision: 1, title: "Документ", values: {}, schema: {sections: []}, permissions: ["read"], files: [{id: "first", name: "Первый", version: 1}, {id: "second", name: "Второй", version: 2}]};
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const data = url.endsWith("/users/me") ? {id: "user", roles: ["viewer"]} : url.endsWith("/registries") ? [{id: "registry", name: "Реестр", permissions: ["read"]}] : url.includes("/content?") ? {items: [], total: 0, page_size: 20} : url.includes("/documents/document?") ? card : url.endsWith("/search") ? {items: [], total: 0} : [];
    return new Response(JSON.stringify(data));
  }));
  function Ready() {return useAuth().user ? <Documents readOnly /> : null;}
  render(<AuthProvider><Ready /></AuthProvider>);
  await waitFor(() => expect((screen.getByLabelText("Файл для просмотра содержания") as HTMLSelectElement).value).toBe("first"));
  window.history.replaceState(null, "", "/view?document=document&tab=content&file=second");
  fireEvent(window, new PopStateEvent("popstate"));
  await waitFor(() => expect((screen.getByLabelText("Файл для просмотра содержания") as HTMLSelectElement).value).toBe("second"));
});
