import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import { Documents } from "./Documents";
import { AuditAdmin } from "./AdminIdentity";
import type { FormSchema } from "../engine/schema";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

function setup(content: "documents" | "audit" = "documents", download = true) {
  const schema: FormSchema = { sections: [{ key: "main", title: "Реквизиты", fields: [
    { key: "title", label: "Название", type: "text" },
    { key: "original", label: "Оригинал", type: "file" },
    { key: "rows", label: "Вложения по строкам", type: "group", children: [
      { key: "attachment", label: "Файл строки", type: "file" },
    ] },
  ] }] };
  const files = [
    { id: "f1", name: "old.txt", version: 1, field_key: "original", size: 3, mime: "text/plain", created_at: "2026-10-15T00:00:00Z" },
    { id: "f2", name: "future.txt", version: 2, field_key: "original", size: 6, mime: "text/plain", created_at: "2026-10-15T01:00:00Z" },
    { id: "g1", name: "row-first.txt", version: 1, field_key: "rows.0.attachment", size: 3, mime: "text/plain", created_at: "2026-10-15T00:00:00Z" },
    { id: "g2", name: "row-second.txt", version: 1, field_key: "rows.1.attachment", size: 3, mime: "text/plain", created_at: "2026-10-15T00:00:00Z" },
  ];
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown;
    if (url.endsWith("/users/me")) data = { id: "u", display_name: "Тест", roles: ["admin"], csrf_token: "csrf" };
    else if (url.includes("/admin/audit")) data = { items: [{ id: "a", created_at: "2026-10-15T00:00:00Z", actor_id: "u", action: "document.created", object_type: "document", object_id: "d", details: {}, request_id: "req" }], total: 1, page: 1, page_size: 30 };
    else if (url.endsWith("/registries")) data = ["A", "B"].map(id => ({ id, name: "Реестр " + id, code: id, document_type_ids: ["t"], permissions: ["read", "update", "upload", "download"] }));
    else if (url.endsWith("/document-types")) data = [{ id: "t", code: "t", name: "Тип" }];
    else if (url.includes("/references")) data = [];
    else if (url.includes("/history")) data = [
      { id: "h1", revision: 1, values: { title: "Документ", original: ["f1"], rows: [{ attachment: ["g2"] }, { attachment: ["g1"] }] }, created_at: "2026-10-15T00:00:00Z", created_by: "u" },
      { id: "h2", revision: 2, values: { title: "Документ", original: ["f1", "f2"] }, created_at: "2026-10-15T01:00:00Z", created_by: "u" },
    ];
    else if (url.includes("/documents/d")) data = { id: "d", registry_id: "A", document_type_id: "t", schema_version_id: "s", title: "Документ", revision: 2, current_version: 2, values: { title: "Документ", original: ["f1", "f2"] }, permissions: ["read", "update", "upload", ...(download ? ["download"] : [])], files, schema };
    else if (url.endsWith("/search")) data = { items: [{ id: "d", title: "Документ", revision: 2 }], total: 1, page: 1, page_size: 20 };
    else throw new Error("Unexpected request: " + url);
    if (url.endsWith("/search") && data && typeof data === "object" && "items" in data) {
      const page = data as {items: unknown[]}; page.items = page.items.map(document => ({document, matches: [], score: 0, match_count: 0}));
    }
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  render(<AuthProvider>{content === "audit" ? <AuditAdmin /> : <Documents readOnly={false} />}</AuthProvider>);
  return fetch;
}

async function openCard() {
  fireEvent.change(await screen.findByLabelText("Реестр", {exact: true}), {target: {value: "A"}});
  fireEvent.click(await screen.findByRole("row", {name: /Выбрать документ/}));
  await screen.findByRole("button", {name: "Сохранить карточку"});
}

it("protects unsaved card edits from a separate file upload", async () => {
  setup();
  await openCard();
  expect(screen.queryByRole("button", {name: "Загрузить новую версию"})).toBeNull();
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Несохранённый текст" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить файл" }));
  expect((screen.getByLabelText(/Добавить файл/) as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText(/Сохраните или отмените изменения карточки/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Карточка" }));
  expect((screen.getByLabelText("Название") as HTMLInputElement).value).toBe("Несохранённый текст");
  fireEvent.click(screen.getByRole("button", { name: "Отмена изменений" }));
  fireEvent.click(screen.getByRole("button", { name: "Файлы и версии" }));
  expect((screen.getByLabelText(/Добавить файл/) as HTMLInputElement).disabled).toBe(false);
});

it("shows only files belonging to each historical revision", async () => {
  setup();
  await openCard();
  fireEvent.click(screen.getByRole("button", { name: "История изменений" }));
  const first = (await screen.findByText(/Редакция 1 ·/)).closest("details")!;
  expect(within(first).getByText("old.txt · версия 1")).toBeTruthy();
  expect(within(first).queryByText("future.txt · версия 2")).toBeNull();
  const firstRow = within(first).getByText("Запись 1").closest(".repeat-row")!;
  expect(within(firstRow as HTMLElement).getByText("row-second.txt · версия 1")).toBeTruthy();
  expect(within(firstRow as HTMLElement).queryByText("row-first.txt · версия 1")).toBeNull();
  const second = screen.getByText(/Редакция 2 ·/).closest("details")!;
  expect(within(second).getByText("future.txt · версия 2")).toBeTruthy();
});

it("does not expose historical file downloads without download permission", async () => {
  setup("documents", false);
  await openCard();
  fireEvent.click(screen.getByRole("button", { name: "История изменений" }));
  const first = (await screen.findByText(/Редакция 1 ·/)).closest("details")!;
  expect(within(first).queryByRole("button")).toBeNull();
  expect(within(first).getByText("old.txt · версия 1")).toBeTruthy();
});

it("renders the object type returned by the audit API", async () => {
  setup("audit");
  const row = (await screen.findByText("document.created")).closest("tr")!;
  expect(within(row).getAllByRole("cell")[2].textContent).toBe("document");
});

it("resets date and sort controls when switching registries to an unfiltered query", async () => {
  const fetch = setup();
  fireEvent.change(await screen.findByLabelText("Реестр", {exact: true}), {target: {value: "A"}});
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  fireEvent.change(screen.getByLabelText("Дата регистрации с"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("Дата регистрации по"), { target: { value: "2026-10-15" } });
  fireEvent.change(screen.getByLabelText("Сортировка"), { target: { value: "title" } });
  fireEvent.click(within(screen.getByRole("dialog", {name: "Расширенный поиск"})).getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/search") && JSON.parse(String(init?.body)).date_from === "2026-10-01")).toBe(true));
  fireEvent.change(screen.getByLabelText("Реестр", {exact: true}), {target: {value: "B"}});
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/search") && JSON.parse(String(init?.body)).registry_ids[0] === "B")).toBe(true));
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  expect((screen.getByLabelText("Дата регистрации с") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("Дата регистрации по") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("Сортировка") as HTMLSelectElement).value).toBe("auto");
  fireEvent.click(within(screen.getByRole("dialog", {name: "Расширенный поиск"})).getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(JSON.parse(String(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).at(-1)?.[1]?.body)).sort).toBe("auto"));
  expect(fetch.mock.calls.at(-1)?.[0]).not.toContain("date_from");
});

it("applies the status filter and clears it when changing registry", async () => {
  const fetch = setup();
  fireEvent.change(await screen.findByLabelText("Реестр", {exact: true}), {target: {value: "A"}});
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  fireEvent.change(screen.getByLabelText("Состояние карточки"), { target: { value: "published" } });
  fireEvent.click(within(screen.getByRole("dialog", {name: "Расширенный поиск"})).getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) =>
    url.endsWith("/search") && JSON.parse(String(init?.body)).card_state === "published",
  )).toBe(true));
  fireEvent.change(screen.getByLabelText("Реестр", {exact: true}), {target: {value: "B"}});
  fireEvent.click(screen.getByRole("button", {name: "Расширенный поиск"}));
  expect((screen.getByLabelText("Состояние карточки") as HTMLInputElement).value).toBe("");
  fireEvent.click(within(screen.getByRole("dialog", {name: "Расширенный поиск"})).getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(JSON.parse(String(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).at(-1)?.[1]?.body)).registry_ids).toEqual(["B"]));
  expect(new URL(fetch.mock.calls.at(-1)![0], "https://example.test").searchParams.has("status")).toBe(false);
});
