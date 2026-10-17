import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DynamicForm } from "./DynamicForm";
import type { Field, ReferenceItem, ReferenceSet, Values } from "./schema";

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

const roleBooks: ReferenceSet[] = [{ id: "people", code: "executors", name: "Исполнители", registry_id: "registry-a" }];
type CreateReference = (field: Field, label: string) => Promise<{ item: ReferenceItem; created: boolean }>;
function Editor({ definition = field, initial = {}, mode = "edit", roles = ["editor"], displayMode, onCreateReference, registryId = "registry-a", references = roleBooks }: {
  definition?: Field; initial?: Values; mode?: "edit" | "view"; roles?: string[]; displayMode?: "editor" | "viewer";
  onCreateReference?: CreateReference; registryId?: string; references?: ReferenceSet[];
}) {
  const [values, setValues] = useState(initial);
  return <form aria-label="Карточка">
    <DynamicForm schema={{ sections: [{ key: "main", title: "", fields: [definition] }] }} values={values} onChange={setValues} mode={mode} roles={roles} displayMode={displayMode} registryId={registryId} references={references} onCreateReference={onCreateReference} />
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

it("creates a missing participant inline and selects it while retaining unsaved values", async () => {
  const create = vi.fn<CreateReference>().mockResolvedValue({ item: { id: "new", label: "Новый Участник", active: true }, created: true });
  render(<Editor initial={{ custom_people: ["anna"], title: "Несохранённый заголовок" }} onCreateReference={create} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "  Новый   Участник  " } });
  const button = screen.getByRole<HTMLButtonElement>("button", { name: "Создать «Новый Участник»" });
  expect(button.type).toBe("button");
  fireEvent.click(button);
  await waitFor(() => expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":["anna","new"],"title":"Несохранённый заголовок"}'));
  expect(create).toHaveBeenCalledWith(expect.objectContaining(field), "Новый Участник");
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Новый Участник" }).checked).toBe(true);
  expect(screen.getByRole("status", { name: "Состояние: Участники" }).textContent).toContain("создан");
});

it.each(["executors", "responsible_executors", "signers", "approvers"])("allows inline creation for the %s book without relying on the field key", async (code) => {
  render(<Editor references={[{ ...roleBooks[0], code }]} onCreateReference={vi.fn()} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Новое имя" } });
  expect(screen.getByRole("button", { name: "Создать «Новое имя»" })).toBeTruthy();
});

it.each([
  { onCreateReference: undefined },
  { references: [{ ...roleBooks[0], code: "departments" }] },
  { references: [{ ...roleBooks[0], registry_id: null }] },
  { definition: { ...field, type: "user" as const } },
])("does not expose creation outside explicitly enabled scoped participant books: %j", async (props) => {
  if ("definition" in props) fetchMock.mockImplementation(async () => new Response(JSON.stringify(people.map(person => ({ ...person, display_name: person.label })))));
  render(<Editor onCreateReference={vi.fn()} {...props} />);
  await screen.findByText("Анна Иванова", { selector: "span, option" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Новое имя" } });
  expect(screen.queryByRole("button", { name: /Создать/ })).toBeNull();
});

it("hides creation for an existing normalized match and explains an inactive exact match", async () => {
  render(<Editor onCreateReference={vi.fn()} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "  аННА   иВАНОВА  " } });
  expect(screen.queryByRole("button", { name: /Создать/ })).toBeNull();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "неактивный СОТРУДНИК" } });
  expect(screen.queryByRole("button", { name: /Создать/ })).toBeNull();
  expect(screen.getByRole("status", { name: "Состояние: Участники" }).textContent).toContain("неактив");
});

it("keeps the draft unchanged on creation failure and permits retry", async () => {
  const create = vi.fn<CreateReference>().mockRejectedValue(new Error("Нет права добавлять участника"));
  render(<Editor initial={{ custom_people: ["anna"] }} onCreateReference={create} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Новое имя" } });
  fireEvent.click(screen.getByRole("button", { name: /Создать/ }));
  expect((await screen.findByRole("alert")).textContent).toContain("Нет права");
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":["anna"]}');
  expect(screen.getByRole<HTMLButtonElement>("button", { name: /Создать/ }).disabled).toBe(false);
});

it("selects a concurrently reused item without adding duplicate selections", async () => {
  const create = vi.fn<CreateReference>().mockResolvedValue({ item: people[0], created: false });
  render(<Editor initial={{ custom_people: ["anna"] }} onCreateReference={create} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Другое написание" } });
  fireEvent.click(screen.getByRole("button", { name: /Создать/ }));
  await waitFor(() => expect(screen.getByRole("status", { name: "Состояние: Участники" }).textContent).toContain("выбран"));
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":["anna"]}');
  expect(screen.getAllByRole("checkbox", { name: "Анна Иванова" })).toHaveLength(1);
});

it("disables creation at the field selection limit", async () => {
  const create = vi.fn<CreateReference>();
  render(<Editor definition={{ ...field, constraints: { max_items: 1 } }} initial={{ custom_people: ["anna"] }} onCreateReference={create} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Новое имя" } });
  const button = screen.getByRole<HTMLButtonElement>("button", { name: /Создать/ });
  expect(button.disabled).toBe(true);
  fireEvent.click(button);
  expect(create).not.toHaveBeenCalled();
});

it("locks lookup controls while creating and ignores a response after the registry changes", async () => {
  let resolve!: (value: { item: ReferenceItem; created: boolean }) => void;
  const create = vi.fn<CreateReference>(() => new Promise((done) => { resolve = done; }));
  const { rerender } = render(<Editor onCreateReference={create} initial={{ custom_people: ["anna"] }} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Новое имя" } });
  fireEvent.click(screen.getByRole("button", { name: /Создать/ }));
  expect(screen.getByRole<HTMLInputElement>("searchbox").disabled).toBe(true);
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Анна Иванова" }).disabled).toBe(true);
  rerender(<Editor onCreateReference={create} registryId="registry-b" initial={{ custom_people: ["anna"] }} />);
  await act(async () => resolve({ item: { id: "wrong", label: "Чужой участник", active: true }, created: true }));
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":["anna"]}');
  expect(screen.queryByRole("checkbox", { name: "Чужой участник" })).toBeNull();
});

it("ignores a pending creation after the card leaves edit mode", async () => {
  let resolve!: (value: { item: ReferenceItem; created: boolean }) => void;
  const create = vi.fn<CreateReference>(() => new Promise(done => { resolve = done; }));
  const { rerender } = render(<Editor onCreateReference={create} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Новое имя" } });
  fireEvent.click(screen.getByRole("button", { name: /Создать/ }));
  rerender(<Editor mode="view" onCreateReference={create} />);
  await act(async () => resolve({ item: { id: "late", label: "Поздний ответ", active: true }, created: true }));
  expect(screen.getByLabelText("Значения").textContent).toBe("{}");
  expect(screen.queryByText("Поздний ответ")).toBeNull();
});

it("does not offer top-level participant creation for fields nested inside repeat groups", async () => {
  render(<Editor definition={{ key: "rows", label: "Строки", type: "group", children: [field] }} initial={{ rows: [{ custom_people: [] }] }} onCreateReference={vi.fn()} />);
  await screen.findByRole("checkbox", { name: "Анна Иванова" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Новое имя" } });
  expect(screen.queryByRole("button", { name: /Создать/ })).toBeNull();
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

it("refreshes reference selectability when a mounted readonly card enters edit mode", async () => {
  const initial = { custom_people: ["anna"] };
  const { rerender } = render(<Editor mode="view" displayMode="editor" initial={initial} />);
  expect(await screen.findByText("Анна Иванова")).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  fetchMock.mockImplementation(async () => new Response(JSON.stringify(people.map((person) =>
    person.id === "anna" || person.id === "boris" ? { ...person, selectable: false } : person,
  ))));

  rerender(<Editor mode="edit" displayMode="editor" initial={initial} />);
  const selected = await screen.findByRole<HTMLInputElement>("checkbox", { name: "Анна Иванова (неактивно)" });
  expect(selected.checked).toBe(true);
  expect(selected.disabled).toBe(false);
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Борис Петров (неактивно)" }).disabled).toBe(true);
  fireEvent.click(selected);
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Анна Иванова (неактивно)" }).disabled).toBe(true);
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":[]}');
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ВЕРА" } });
  expect(screen.getByRole("checkbox", { name: "Вера Соколова" })).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(2);
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
  render(<Editor mode={mode} definition={definition} initial={{ custom_people: ["anna"] }} onCreateReference={vi.fn()} />);
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

it("resolves the current document registry's reference code even when foreign and global sets arrive first", async () => {
  fetchMock.mockImplementation(async (url) => new Response(JSON.stringify([
    { id: String(url).includes("people-a") ? "person-a" : "person-b", label: String(url).includes("people-a") ? "Исполнитель А" : "Исполнитель Б" },
  ])));
  const schema = { sections: [{ key: "main", title: "", fields: [{ ...field, reference_set_id: undefined, reference_code: "executors" }] }] };
  const references = [{ id: "people-b", code: "executors", name: "Б", registry_id: "registry-b" },
    { id: "people-global", code: "executors", name: "Общий", registry_id: null },
    { id: "people-a", code: "executors", name: "А", registry_id: "registry-a" }];
  const { rerender } = render(<DynamicForm schema={schema} values={{}} mode="edit" roles={["editor"]} registryId="registry-a" references={references} />);
  expect(await screen.findByRole("checkbox", { name: "Исполнитель А" })).toBeTruthy();
  expect(screen.queryByRole("checkbox", { name: "Исполнитель Б" })).toBeNull();
  rerender(<DynamicForm schema={schema} values={{}} mode="edit" roles={["editor"]} registryId="registry-b" references={references} />);
  expect(await screen.findByRole("checkbox", { name: "Исполнитель Б" })).toBeTruthy();
  expect(screen.queryByRole("checkbox", { name: "Исполнитель А" })).toBeNull();
  expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
    expect.stringContaining("/references/people-a/items"), expect.stringContaining("/references/people-b/items"),
  ]);
});

it("disambiguates participants and searches approved business metadata", async () => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify([
    { id: "one", label: "Анна Иванова", active: true, metadata: { position: "Юрист", department: "Правовой отдел", source_attributes: { secret: "private" } } },
    { id: "two", label: "Борис Петров", active: true, metadata: { position: "Начальник", department: "Канцелярия" } },
  ])));
  render(<Editor />);
  await screen.findByRole("checkbox", { name: "Анна Иванова · Юрист · Правовой отдел" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "правовой" } });
  expect(screen.getByRole("checkbox", { name: "Анна Иванова · Юрист · Правовой отдел" })).toBeTruthy();
  expect(screen.queryByRole("checkbox", { name: /Борис/ })).toBeNull();
  expect(screen.queryByText(/private/)).toBeNull();
});

it("does not load an explicit reference set belonging to a different registry", async () => {
  render(<Editor references={[{ ...roleBooks[0], registry_id: "registry-b" }]} />);
  await waitFor(() => expect(screen.getByRole("status", { name: "Состояние: Участники" }).textContent).toContain("Нет доступных"));
  expect(fetchMock).not.toHaveBeenCalled();
});
it("does not offer duplicate creation for an inactive participant with business metadata", async () => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify([
    { id: "inactive", label: "Анна Иванова", active: false, metadata: { position: "Юрист" } },
  ])));
  render(<Editor onCreateReference={vi.fn()} />);
  await screen.findByRole("checkbox", { name: /Анна Иванова/ });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Анна Иванова" } });
  expect(screen.queryByRole("button", { name: /Создать/ })).toBeNull();
  expect(screen.getByRole("status", { name: "Состояние: Участники" }).textContent).toContain("неактивен");
});
