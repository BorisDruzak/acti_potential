import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { Documents } from "./Documents";

afterEach(() => {cleanup(); vi.unstubAllGlobals(); localStorage.clear();});

function setup() {
  const fetch = vi.fn(async (url: string) => {
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "reader", display_name: "Reader", roles: ["viewer"], csrf_token: "csrf"};
    else if (url.endsWith("/registries")) data = [{id: "r", code: "r", name: "Реестр", permissions: ["read"], document_type_ids: ["t"]}];
    else if (url.endsWith("/document-types")) data = [{id: "t", code: "t", name: "Тип"}];
    else if (url.includes("/versions")) data = [{id: "s", state: "published", version: 1, document_type_id: "t", schema: {sections: [{key: "main", title: "Поля", fields: [
      {key: "amount", label: "Сумма", type: "decimal", filterable: true, exportable: true},
      {key: "note", label: "Примечание", type: "text", exportable: false},
      {key: "conditional", label: "Условное поле", type: "text", filterable: true, visible_when: {field: "amount", op: "present"}},
    ]}], list_view: {columns: [{key: "amount", label: "Размер"}, {key: "note", label: "Описание"}]}}}];
    else if (url.includes("/documents/export?")) return new Response("export", {headers: {"Content-Type": "text/csv"}});
    else if (url.includes("/documents?")) data = {items: [{id: "d", title: "Документ", revision: 1, values: {amount: "1.20", note: "Открыто"}, display_values: {amount: "1.20", note: "Открыто"}}], total: 1, page: 1, page_size: 20};
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("URL", class extends URL {static createObjectURL() {return "blob:download";} static revokeObjectURL() {}});
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  function Ready() {return useAuth().user ? <Documents readOnly /> : null;}
  render(<AuthProvider><Ready /></AuthProvider>);
  return fetch;
}

it("applies decimal ranges, versioned list columns and exports only permitted selected columns", async () => {
  const fetch = setup();
  fireEvent.click(await screen.findByRole("button", {name: "Реестр"}));
  fireEvent.change(screen.getByLabelText("Тип"), {target: {value: "t"}});
  fireEvent.change(await screen.findByLabelText("Дополнительный фильтр"), {target: {value: "amount"}});
  expect(screen.queryByRole("option", {name: "Условное поле"})).toBeNull();
  fireEvent.change(screen.getByLabelText("Сравнение"), {target: {value: "between"}});
  fireEvent.change(screen.getByLabelText("Значение фильтра"), {target: {value: "1.20"}});
  fireEvent.change(screen.getByLabelText("Верхняя граница"), {target: {value: "2.50"}});
  fireEvent.change(screen.getByLabelText("Сортировка"), {target: {value: "custom:amount"}});
  fireEvent.change(screen.getByLabelText("Направление сортировки"), {target: {value: "asc"}});
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  await screen.findByRole("columnheader", {name: "Размер"});
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("filters=") && JSON.parse(new URL(url, "https://test.invalid").searchParams.get("filters")!).amount.value[0] === "1.20")).toBe(true));
  fireEvent.click(screen.getByRole("button", {name: "Экспорт CSV"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("/documents/export?") && new URL(url, "https://test.invalid").searchParams.get("columns") === "amount")).toBe(true));
  const exported = fetch.mock.calls.find(([url]) => url.includes("/documents/export?"))![0];
  expect(new URL(exported, "https://test.invalid").searchParams.get("sort")).toBe("custom:amount");
  expect(screen.getByText("Открыто")).toBeTruthy();
});

it("saves and restores filters per user and workspace without storing result data", async () => {
  setup();
  fireEvent.click(await screen.findByRole("button", {name: "Реестр"}));
  fireEvent.change(screen.getByLabelText("Поиск"), {target: {value: "Образец"}});
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  fireEvent.change(screen.getByLabelText("Название представления"), {target: {value: "Мой поиск"}});
  fireEvent.click(screen.getByRole("button", {name: "Сохранить текущий поиск"}));
  const saved = JSON.parse(localStorage.getItem("acti.views.v1.reader.viewer")!);
  expect(saved[0]).toEqual({name: "Мой поиск", registry: "r", query: "q=%D0%9E%D0%B1%D1%80%D0%B0%D0%B7%D0%B5%D1%86&sort=updated_at&direction=desc"});
  fireEvent.change(screen.getByLabelText("Поиск"), {target: {value: "Другое"}});
  fireEvent.click(screen.getByRole("button", {name: "Мой поиск"}));
  expect((screen.getByLabelText("Поиск") as HTMLInputElement).value).toBe("Образец");
  expect(localStorage.getItem("acti.views.v1.reader.editor")).toBeNull();
});
