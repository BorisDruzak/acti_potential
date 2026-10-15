import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import { Documents } from "./Documents";
import { AuditAdmin } from "./AdminIdentity";
import type { FormSchema } from "../engine/schema";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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
  const fetch = vi.fn(async (url: string) => {
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
    else if (url.includes("/documents?")) data = { items: [{ id: "d", title: "Документ", revision: 2 }], total: 1, page: 1, page_size: 20 };
    else throw new Error("Unexpected request: " + url);
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  render(<AuthProvider>{content === "audit" ? <AuditAdmin /> : <Documents readOnly={false} />}</AuthProvider>);
  return fetch;
}

async function openCard() {
  fireEvent.click(await screen.findByRole("button", { name: "Реестр A" }));
  fireEvent.click(await screen.findByRole("button", { name: "Открыть карточку" }));
  await screen.findByText("UUID: d");
}

it("protects unsaved card edits from a separate file upload", async () => {
  setup();
  await openCard();
  fireEvent.click(screen.getByRole("button", { name: "Редактировать карточку" }));
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Несохранённый текст" } });
  fireEvent.click(screen.getByRole("button", { name: "Файлы и версии" }));
  expect((screen.getByLabelText(/Добавить файл или следующую версию/) as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText(/Сохраните или отмените изменения карточки/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Карточка" }));
  expect((screen.getByLabelText("Название") as HTMLInputElement).value).toBe("Несохранённый текст");
  fireEvent.click(screen.getByRole("button", { name: "Отмена изменений" }));
  fireEvent.click(screen.getByRole("button", { name: "Файлы и версии" }));
  expect((screen.getByLabelText(/Добавить файл или следующую версию/) as HTMLInputElement).disabled).toBe(false);
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
  fireEvent.click(await screen.findByRole("button", { name: "Реестр A" }));
  fireEvent.change(screen.getByLabelText("Дата с"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("Дата по"), { target: { value: "2026-10-15" } });
  fireEvent.change(screen.getByLabelText("Сортировка"), { target: { value: "title" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("date_from=2026-10-01"))).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Реестр B" }));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("registry_id=B"))).toBe(true));
  expect((screen.getByLabelText("Дата с") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("Дата по") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("Сортировка") as HTMLSelectElement).value).toBe("updated_at");
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  await waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).toContain("sort=updated_at"));
  expect(fetch.mock.calls.at(-1)?.[0]).not.toContain("date_from");
});

it("applies the status filter and clears it when changing registry", async () => {
  const fetch = setup();
  fireEvent.click(await screen.findByRole("button", { name: "Реестр A" }));
  fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "Зарегистрирован" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) =>
    new URL(url, "https://example.test").searchParams.get("status") === "Зарегистрирован",
  )).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Реестр B" }));
  expect((screen.getByLabelText("Статус") as HTMLInputElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  await waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).toContain("registry_id=B"));
  expect(new URL(fetch.mock.calls.at(-1)![0], "https://example.test").searchParams.has("status")).toBe(false);
});
