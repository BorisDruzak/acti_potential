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
