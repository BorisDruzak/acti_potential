import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DynamicForm } from "./DynamicForm";
import type { Field, Values } from "./schema";

const people = [
  { id: "anna", label: "Анна Иванова", active: true },
  { id: "boris", label: "Борис Петров", active: true },
  { id: "vera", label: "Вера Соколова", active: true },
  { id: "inactive", label: "Неактивный сотрудник", active: true, selectable: false },
];
const field: Field = { key: "custom_people", label: "Участники", type: "multi_reference", reference_set_id: "people" };
const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => new Response(JSON.stringify(people)));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Editor({ definition = field, initial = {}, mode = "edit", roles = ["editor"] }: {
  definition?: Field; initial?: Values; mode?: "edit" | "view"; roles?: string[];
}) {
  const [values, setValues] = useState(initial);
  return <form aria-label="Карточка">
    <DynamicForm schema={{ sections: [{ key: "main", title: "", fields: [definition] }] }} values={values} onChange={setValues} mode={mode} roles={roles} />
    <output aria-label="Значения">{JSON.stringify(values)}</output>
  </form>;
}

it("toggles multiple people by normal click and keeps selected people while filtering Russian names", async () => {
  render(<Editor />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Анна Иванова" }));
  fireEvent.change(screen.getByRole("searchbox", { name: "Поиск: Участники" }), { target: { value: "пЕТР" } });
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Анна Иванова" }).checked).toBe(true);
  expect(screen.queryByRole("checkbox", { name: "Вера Соколова" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Борис Петров" }));
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":["anna","boris"]}');
  fireEvent.click(screen.getByRole("checkbox", { name: "Анна Иванова" }));
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":["boris"]}');
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("lets existing inactive and missing choices be removed but prevents selecting inactive choices again", async () => {
  render(<Editor initial={{ custom_people: ["inactive", "missing"] }} />);
  const inactive = await screen.findByRole<HTMLInputElement>("checkbox", { name: "Неактивный сотрудник (неактивно)" });
  expect(inactive.disabled).toBe(false);
  fireEvent.click(inactive);
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Неактивный сотрудник (неактивно)" }).disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "missing (неактивно)" }));
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":[]}');
});

it("requires a selection even after typing a query and enforces the selection limit without blocking removal", async () => {
  render(<Editor definition={{ ...field, required: true, constraints: { max_items: 1 } }} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  const form = screen.getByRole<HTMLFormElement>("form", { name: "Карточка" });
  expect(form.checkValidity()).toBe(false);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Анна" } });
  expect(form.checkValidity()).toBe(false);
  fireEvent.click(screen.getByRole("checkbox", { name: "Анна Иванова" }));
  expect(form.checkValidity()).toBe(true);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Борис Петров" }).disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "Анна Иванова" }));
  expect(form.checkValidity()).toBe(false);
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Борис Петров" }).disabled).toBe(false);
});

it("filters single references locally while retaining the current selection and supports clearing it", async () => {
  render(<Editor definition={{ ...field, type: "reference" }} initial={{ custom_people: "anna" }} />);
  await screen.findByRole("option", { name: "Борис Петров" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ПЕТРОВ" } });
  expect(screen.getByRole<HTMLSelectElement>("combobox").value).toBe("anna");
  expect(screen.queryByRole("option", { name: "Вера Соколова" })).toBeNull();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "boris" } });
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":"boris"}');
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "" } });
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":null}');
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("keeps document searching on the remote endpoint", async () => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ items: [{ id: "doc", title: "Документ" }] })));
  render(<Editor definition={{ ...field, type: "document" }} />);
  await screen.findByRole("option", { name: "Документ" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "акт" } });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(String(fetchMock.mock.calls[1][0])).toContain("q=%D0%B0%D0%BA%D1%82");
});

it("announces loading, unavailable choices, and request errors without allowing new selections", async () => {
  let resolve!: (value: Response) => void;
  fetchMock.mockImplementation(() => new Promise<Response>((done) => { resolve = done; }));
  render(<Editor />);
  expect(screen.getByRole("status", { name: "Состояние: Участники" }).textContent).toContain("Загрузка");
  await act(async () => { resolve(new Response("[]")); });
  expect(screen.getByRole("status", { name: "Состояние: Участники" }).textContent).toContain("Нет доступных значений");
  cleanup();
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ error: { message: "Доступ запрещён" } }), { status: 403 }));
  render(<Editor />);
  expect((await screen.findByRole("alert")).textContent).toContain("Доступ запрещён");
  expect(screen.queryByRole("checkbox")).toBeNull();
});

it.each([
  { mode: "view" as const, definition: field },
  { mode: "edit" as const, definition: { ...field, editable: false } },
  { mode: "edit" as const, definition: { ...field, write_roles: ["admin"] } },
])("keeps $mode readonly references free from mutation controls", async ({ mode, definition }) => {
  render(<Editor mode={mode} definition={definition} initial={{ custom_people: ["anna"] }} />);
  expect(await screen.findByText("Анна Иванова")).toBeTruthy();
  expect(screen.queryByRole("searchbox")).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByRole("combobox")).toBeNull();
});

it("does not fetch or render references hidden by field read permissions", () => {
  render(<Editor definition={{ ...field, read_roles: ["admin"] }} />);
  expect(screen.queryByText("Участники")).toBeNull();
  expect(fetchMock).not.toHaveBeenCalled();
});
