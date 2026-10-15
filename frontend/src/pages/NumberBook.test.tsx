import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { editableValues, type FormSchema } from "../engine/schema";
import { DynamicForm } from "../engine/DynamicForm";
import { CardNumberAssignment, NumberBook } from "./NumberBook";
import type { Card } from "../engine/schema";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});

const series = {
  id: "series", code: "test", name: "Тестовая серия", registry_id: "registry", document_type_ids: [],
  state: "active", reservation_hours: 24, max_reservation_hours: 168, max_batch: 3,
  allow_indefinite: false, permissions: ["view", "reserve", "assign", "cancel", "void"],
};
const schema: FormSchema = {sections: [{key: "main", title: "Основное", fields: [
  {key: "number", label: "Номер из серии", type: "text", system: "reg_number", number_series_id: "series", required: true},
  {key: "ordinary", label: "Обычный реквизит", type: "text"},
]}]};

function setup({reader = false, failFirst = false, card = false, expired = false} = {}) {
  let firstReserve = true;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "user", display_name: "User", roles: [reader ? "viewer" : "editor"], csrf_token: "csrf"};
    else if (url.endsWith("/number-series")) data = [{...series, permissions: reader ? ["view"] : series.permissions}];
    else if (url.includes("/stats")) data = {counts: {reserved: 1}, period_key: "2026", next_number: "2026/0002"};
    else if (url.includes("/entries?")) data = {items: [{id: "entry", series_id: "series", number: "2026/0001", period_key: "2026", state: "reserved", expires_at: expired ? "2020-01-01T00:00:00Z" : null}], total: 1};
    else if (url.endsWith("/reserve")) {
      if (firstReserve && failFirst) {firstReserve = false; throw new Error("network");}
      data = {items: [{id: "entry", number: "2026/0001"}]};
    } else if (url.endsWith("/assign")) data = {};
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  const saved = vi.fn();
  function Ready() {return useAuth().user ? card ? <CardNumberAssignment card={{id: "document", schema, registry_id: "registry", revision: 7, values: {}, permissions: ["read", "update"]} as Card} onSaved={saved}/> : <NumberBook readOnly={reader}/> : null;}
  render(<AuthProvider><Ready/></AuthProvider>);
  return {fetch, saved};
}

it("retries an uncertain reservation with the identical request key and payload", async () => {
  const {fetch} = setup({failFirst: true});
  await screen.findByRole("option", {name: "Тестовая серия · test"});
  fireEvent.change(await screen.findByLabelText("Серия номеров"), {target: {value: "series"}});
  fireEvent.change(await screen.findByLabelText("Количество номеров"), {target: {value: "3"}});
  fireEvent.click(screen.getByRole("button", {name: "Зарезервировать"}));
  await screen.findByRole("alert");
  fireEvent.change(screen.getByLabelText("Количество номеров"), {target: {value: "1"}});
  fireEvent.click(screen.getByRole("button", {name: "Повторить запрос резерва"}));
  await screen.findByText("Результат резервирования: 2026/0001");
  const requests = fetch.mock.calls.filter(([url]) => url.endsWith("/reserve"));
  expect(requests).toHaveLength(2);
  expect(requests[0][1]?.body).toBe(requests[1][1]?.body);
  expect(JSON.parse(String(requests[1][1]?.body)).count).toBe(3);
  expect((screen.getByLabelText("Количество номеров") as HTMLInputElement).max).toBe("3");
});

it("reader sees the authorized journal and no numbering mutation controls", async () => {
  setup({reader: true});
  await screen.findByRole("option", {name: "Тестовая серия · test"});
  fireEvent.change(await screen.findByLabelText("Серия номеров"), {target: {value: "series"}});
  await screen.findByText("2026/0001");
  expect(screen.queryByRole("button", {name: "Зарезервировать"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Присвоить номер"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Отменить резерв"})).toBeNull();
  expect(screen.getByRole("button", {name: "Экспорт номеров XLSX"})).toBeTruthy();
});

it("omits bound registration numbers from manual writes while ordinary fields stay editable", () => {
  const values = {number: "MANUAL", ordinary: "Данные"};
  expect(editableValues(schema, values, "create", ["editor"])).toEqual({ordinary: "Данные"});
  render(<DynamicForm schema={schema} values={{}} onChange={vi.fn()} mode="create" roles={["editor"]}/>);
  expect(screen.queryByRole("textbox", {name: /Номер из серии/})).toBeNull();
  expect(screen.getByText("Номер присваивается из резерва после создания карточки")).toBeTruthy();
  expect(screen.getByRole("textbox", {name: "Обычный реквизит"})).toBeTruthy();
});

it("assigns a reserved number through the server using the current document revision", async () => {
  const {fetch, saved} = setup({card: true});
  await screen.findByRole("option", {name: "2026/0001"});
  fireEvent.change(await screen.findByLabelText("Резерв для карточки"), {target: {value: "entry"}});
  fireEvent.click(screen.getByRole("button", {name: "Присвоить номер карточке"}));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  const request = fetch.mock.calls.find(([url]) => url.endsWith("/number-entries/entry/assign"));
  expect(JSON.parse(String(request![1]?.body))).toMatchObject({document_id: "document", revision: 7});
  expect(JSON.parse(String(request![1]?.body)).idempotency_key).toBeTruthy();
});

it("records an elapsed reservation only after an explicit operation with a reason", async () => {
  const {fetch} = setup({expired: true});
  await screen.findByRole("option", {name: "Тестовая серия · test"});
  fireEvent.change(screen.getByLabelText("Серия номеров"), {target: {value: "series"}});
  fireEvent.click(await screen.findByRole("button", {name: "Зафиксировать истечение"}));
  expect(fetch.mock.calls.some(([url]) => url.endsWith("/expire"))).toBe(false);
  expect(screen.getByRole("heading", {name: "Истечение срока резерва: 2026/0001"})).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Причина операции"), {target: {value: "Срок резерва завершён"}});
  fireEvent.click(screen.getByRole("button", {name: "Подтвердить операцию"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith("/number-entries/entry/expire"))).toBe(true));
  const request = fetch.mock.calls.find(([url]) => url.endsWith("/number-entries/entry/expire"));
  expect(JSON.parse(String(request![1]?.body))).toMatchObject({reason: "Срок резерва завершён"});
});
