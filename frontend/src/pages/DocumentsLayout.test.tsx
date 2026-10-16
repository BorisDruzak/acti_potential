import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { Documents } from "./Documents";

afterEach(() => {cleanup(); vi.unstubAllGlobals(); localStorage.clear(); window.history.replaceState(null, "", "/work");});
function setup(mixedVersions = false) {
  let revision = 1;
  const schema = {sections: [{key: "main", title: "Основное", fields: [
    {key: "caption", label: "Наименование", type: "text", system: "title", editable: true},
    {key: "x_kind", label: "Категория", type: "text", filterable: true},
    {key: "x_topic", label: "Содержание", type: "text", filterable: true},
    {key: "x_owner", label: "Куратор", type: "user"},
    {key: "private", label: "Закрыто", type: "text", read_roles: ["admin"]},
  ]}], list_view: {columns: [{key: "x_kind", label: "Вид документа"}, {key: "x_topic", label: "Тема"}, {key: "x_owner", label: "Ответственный исполнитель"}]}};
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "layout-user", roles: ["editor"], csrf_token: "csrf"};
    else if (url.endsWith("/registries")) data = [{id: "r", name: "Реестр", document_type_ids: ["type"], permissions: ["read", "update"]}];
    else if (url.includes("/document-types/type/versions")) {
      const newer = JSON.parse(JSON.stringify(schema));
      newer.sections[0].fields.find((field: {key: string}) => field.key === "x_topic").key = "new_topic";
      newer.list_view.columns.find((column: {key: string}) => column.key === "x_topic").key = "new_topic";
      data = [{id: "schema", state: "published", document_type_id: "type", version: 1, schema},
        ...(mixedVersions ? [{id: "schema2", state: "published", document_type_id: "type", version: 2, schema: newer}] : [])];
    }
    else if (url.endsWith("/document-types")) data = [{id: "type", name: "Приказ"}];
    else if (url.endsWith("/search")) data = {items: ["a", "b"].map((id, index) => ({document: {id, document_type_id: "type", schema_version_id: "schema", title: index ? "Бета" : "Альфа", reg_number: "Р-" + (index + 1), reg_date: "2026-10-16", values: {caption: "Альфа", x_kind: "Распоряжение", x_topic: "Благоустройство", x_owner: "opaque-id"}, display_values: {x_kind: "Распоряжение", x_topic: "Благоустройство", x_owner: "Сотрудник"}}, matches: []})), total: 40, next_cursor: "page-two", page_size: 20};
    else if (url.includes("/documents/a/search-index")) data = {ready: true};
    else if (url.includes("/documents/a")) {
      if (init?.method === "PUT") revision++;
      data = {id: "a", registry_id: "r", revision, current_version: revision, title: "Альфа", reg_number: "Р-1", values: {caption: "Альфа"}, schema, permissions: ["read", "update"], files: []};
    }
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  function Ready() {return useAuth().user ? <Documents/> : null;}
  render(<AuthProvider><Ready/></AuthProvider>);
  return fetch;
}
const mode = (name: string) => screen.getByRole("radio", {name});
const firstRow = () => screen.findByRole("row", {name: /Выбрать документ «Альфа»/});

it("uses one three-position control, selects on click and opens full card on double-click or Enter", async () => {
  setup();
  expect(await screen.findByRole("radio", {name: "Вместе"})).toHaveProperty("ariaChecked", "true");
  expect(screen.queryByRole("button", {name: "Открыть карточку"})).toBeNull();
  fireEvent.click(await firstRow());
  await screen.findByRole("button", {name: "Редактировать карточку"});
  expect(mode("Вместе").getAttribute("aria-checked")).toBe("true");
  fireEvent.click(mode("Таблица"));
  fireEvent.doubleClick(await firstRow());
  expect(mode("Карточка").getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("button", {name: "К списку"}));
  expect(mode("Таблица").getAttribute("aria-checked")).toBe("true");
  fireEvent.keyDown(await firstRow(), {key: "Enter"});
  expect(mode("Карточка").getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("button", {name: "Показать список документов"}));
  expect(mode("Вместе").getAttribute("aria-checked")).toBe("true");
});
it("keeps one mounted card and its draft, active tab, selection, query and result page across modes", async () => {
  const fetch = setup();
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  fireEvent.change(screen.getByLabelText("Наименование"), {target: {value: "Черновик"}});
  fireEvent.change(screen.getByRole("searchbox", {name: /Поиск/}), {target: {value: "Запрос"}});
  fireEvent.click(screen.getByRole("button", {name: "Вперёд"}));
  const requests = fetch.mock.calls.filter(([url]) => url.includes("/documents/a?")).length;
  for (const name of ["Таблица", "Карточка", "Вместе"]) fireEvent.click(mode(name));
  expect((screen.getByLabelText("Наименование") as HTMLInputElement).value).toBe("Черновик");
  expect((screen.getByRole("searchbox", {name: /Поиск/}) as HTMLInputElement).value).toBe("Запрос");
  expect(screen.getByText("Страница 2")).toBeTruthy();
  expect(fetch.mock.calls.filter(([url]) => url.includes("/documents/a?")).length).toBe(requests);
  fireEvent.click(screen.getByRole("button", {name: "Файлы и версии"}));
  fireEvent.click(mode("Карточка")); fireEvent.click(mode("Вместе"));
  expect(screen.getByRole("button", {name: "Файлы и версии"}).className).toContain("active");
});
it("renders seven columns from each document's configured schema and sorts via the existing API", async () => {
  const fetch = setup();
  fireEvent.click(await screen.findByRole("radio", {name: "Таблица"}));
  await screen.findAllByText("Благоустройство");
  expect(screen.getAllByRole("columnheader")).toHaveLength(7);
  const row = within(await firstRow());
  expect(row.getByText("Сотрудник")).toBeTruthy();
  expect(row.queryByText("opaque-id")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "Сортировать: Наименование документа"}));
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).some(([, init]) => JSON.parse(String(init?.body)).sort === "title")).toBe(true));
  expect(localStorage.getItem("acti.documents.layout.v1.layout-user.editor")).toContain("table");
});

it("does not offer a page-dependent custom sort when another published version uses a different field", async () => {
  window.history.replaceState(null, "", "/work?document_type_id=type");
  setup(true);
  fireEvent.click(await screen.findByRole("radio", {name: "Таблица"}));
  await screen.findAllByText("Благоустройство");
  expect(screen.queryByRole("button", {name: "Сортировать: Тема"})).toBeNull();
  expect(screen.getByRole("button", {name: "Сортировать: Вид документа"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Вперёд"}));
  await screen.findByText("Страница 2");
  expect(screen.queryByRole("button", {name: "Сортировать: Тема"})).toBeNull();
});
