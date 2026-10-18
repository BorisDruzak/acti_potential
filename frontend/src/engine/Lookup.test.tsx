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
function Editor({ definition = field, initial = {}, mode = "edit", roles = ["editor"], displayMode, onCreateReference, registryId = "registry-a", references = roleBooks, referenceScope }: {
  definition?: Field; initial?: Values; mode?: "edit" | "view"; roles?: string[]; displayMode?: "editor" | "viewer";
  onCreateReference?: CreateReference; registryId?: string; references?: ReferenceSet[]; referenceScope?: "registry" | "available";
}) {
  const [values, setValues] = useState(initial);
  return <form aria-label="Карточка">
    <DynamicForm schema={{ sections: [{ key: "main", title: "", fields: [definition] }] }} values={values} onChange={setValues} mode={mode} roles={roles} displayMode={displayMode} registryId={registryId} references={references} referenceScope={referenceScope} onCreateReference={onCreateReference} />
    <output aria-label="Значения">{JSON.stringify(values)}</output>
  </form>;
}

it("opens one editable portal combobox and auto-selects only an existing unique match after three characters", async () => {
  const { container } = render(<Editor />);
  const input = screen.getByRole<HTMLInputElement>("combobox");
  expect(container.querySelectorAll("input")).toHaveLength(1);
  expect(container.querySelector("select")).toBeNull();
  expect(screen.queryByRole("listbox")).toBeNull();
  fireEvent.focus(input);
  await screen.findByRole("option", { name: "Анна Иванова" });
  expect(container.contains(screen.getByRole("listbox"))).toBe(false);
  fireEvent.change(input, { target: { value: "Ан" } });
  expect(screen.getByLabelText("Значения").textContent).toBe("{}");
  fireEvent.change(input, { target: { value: "Анн" } });
  expect(screen.getByLabelText("Значения").textContent).toBe('{"custom_people":["anna"]}');
  expect(screen.getByRole("button", { name: "Убрать Анна Иванова" })).toBeTruthy();
});

async function openLookup() {
  const input = screen.getByRole<HTMLInputElement>("combobox");
  fireEvent.focus(input);
  await waitFor(() => expect(screen.getByRole("listbox").getAttribute("aria-busy")).toBe("false"));
  return input;
}
const values = () => screen.getByLabelText("Значения").textContent;
const status = () => screen.getByRole("status", { name: "Состояние: Участники" }).textContent;

it("filters ambiguous names and commits UUIDs by mouse or keyboard while preserving chips", async () => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify([...people, { id: "anna-two", label: "Анна Смирнова", active: true }])));
  render(<Editor initial={{ custom_people: ["boris"] }} />);
  const input = await openLookup();
  fireEvent.change(input, { target: { value: "Анн" } });
  expect(values()).toBe('{"custom_people":["boris"]}');
  expect(screen.queryByRole("option", { name: "Вера Соколова" })).toBeNull();
  fireEvent.keyDown(input, { key: "ArrowDown" });
  expect(input.getAttribute("aria-activedescendant")).toContain("anna");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(values()).toBe('{"custom_people":["boris","anna"]}');
  fireEvent.click(screen.getByRole("option", { name: "Вера Соколова" }));
  expect(values()).toBe('{"custom_people":["boris","anna","vera"]}');
  fireEvent.click(screen.getByRole("button", { name: "Убрать Борис Петров" }));
  expect(values()).toBe('{"custom_people":["anna","vera"]}');
  fireEvent.keyDown(input, { key: "Backspace" });
  expect(values()).toBe('{"custom_people":["anna"]}');
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("never commits arbitrary text, inactive matches, selected matches, or a short query", async () => {
  const create = vi.fn<CreateReference>();
  render(<Editor initial={{ custom_people: ["anna"] }} onCreateReference={create} />);
  const input = await openLookup();
  for (const query of ["Анна", "Неактивный", "Несуществующий", "Бо"]) {
    fireEvent.change(input, { target: { value: query } });
    expect(values()).toBe('{"custom_people":["anna"]}');
  }
  fireEvent.keyDown(input, { key: "Enter" });
  expect(values()).toBe('{"custom_people":["anna"]}');
  expect(create).not.toHaveBeenCalled();
});

it.each(["reference", "user", "document"] as const)("uses one input for %s and permits clearing only the stored selection", async type => {
  if (type === "user") fetchMock.mockImplementation(async () => new Response(JSON.stringify(people.map(p => ({ id: p.id, display_name: p.label })))));
  if (type === "document") fetchMock.mockImplementation(async () => new Response(JSON.stringify({ items: [{ id: "boris", title: "Борис Петров" }] })));
  render(<Editor definition={{ ...field, type, required: true }} />);
  const input = await openLookup();
  expect(screen.getByRole<HTMLFormElement>("form").checkValidity()).toBe(false);
  fireEvent.change(input, { target: { value: "Пет" } });
  await waitFor(() => expect(values()).toBe('{"custom_people":"boris"}'));
  fireEvent.keyDown(input, { key: "Escape" });
  expect(input.value).toBe("Борис Петров");
  expect(screen.getByRole<HTMLFormElement>("form").checkValidity()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Убрать Борис Петров" }));
  expect(values()).toBe('{"custom_people":null}');
  expect(screen.getByRole<HTMLFormElement>("form").checkValidity()).toBe(false);
});

it("retains inactive and missing historical chips for removal and never reselects them", async () => {
  render(<Editor initial={{ custom_people: ["inactive", "missing"] }} />);
  const input = await openLookup();
  expect(screen.getByRole("button", { name: "Убрать Неактивный сотрудник" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "Неактивный сотрудник (неактивно) ✓" }).getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Убрать Неактивный сотрудник" }));
  fireEvent.click(screen.getByRole("button", { name: "Убрать missing" }));
  fireEvent.click(screen.getByRole("option", { name: "Неактивный сотрудник (неактивно)" }));
  fireEvent.change(input, { target: { value: "Неактивный" } });
  expect(values()).toBe('{"custom_people":[]}');
});

it("enforces required selections and maximum count while permitting removal", async () => {
  render(<Editor definition={{ ...field, required: true, constraints: { max_items: 1 } }} />);
  const input = await openLookup();
  const form = screen.getByRole<HTMLFormElement>("form");
  fireEvent.change(input, { target: { value: "Не найден" } });
  expect(form.checkValidity()).toBe(false);
  fireEvent.change(input, { target: { value: "Анн" } });
  expect(form.checkValidity()).toBe(true);
  expect(screen.getByRole("option", { name: "Борис Петров" }).getAttribute("aria-disabled")).toBe("true");
  fireEvent.change(input, { target: { value: "Бор" } });
  expect(values()).toBe('{"custom_people":["anna"]}');
  fireEvent.click(screen.getByRole("button", { name: "Убрать Анна Иванова" }));
  expect(form.checkValidity()).toBe(false);
});

it("closes with Escape or blur without changing stored values and reopens by keyboard", async () => {
  render(<Editor initial={{ custom_people: ["anna"] }} />);
  const input = await openLookup();
  fireEvent.change(input, { target: { value: "unknown" } });
  fireEvent.keyDown(input, { key: "Escape" });
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(input.getAttribute("aria-expanded")).toBe("false");
  fireEvent.keyDown(input, { key: "ArrowDown" });
  expect(screen.getByRole("listbox")).toBeTruthy();
  fireEvent.blur(input);
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(values()).toBe('{"custom_people":["anna"]}');
});

it("persists a participant only through the explicit create action and retains unsaved draft fields", async () => {
  const create = vi.fn<CreateReference>().mockResolvedValue({ item: { id: "new", label: "Новый Участник", active: true }, created: true });
  render(<Editor initial={{ custom_people: ["anna"], title: "Несохранённый заголовок" }} onCreateReference={create} />);
  fireEvent.change(await openLookup(), { target: { value: "  Новый   Участник  " } });
  expect(create).not.toHaveBeenCalled();
  const button = screen.getByRole<HTMLButtonElement>("button", { name: "Создать «Новый Участник»" });
  expect(button.type).toBe("button");
  fireEvent.click(button);
  await waitFor(() => expect(values()).toBe('{"custom_people":["anna","new"],"title":"Несохранённый заголовок"}'));
  expect(create).toHaveBeenCalledWith(expect.objectContaining(field), "Новый Участник");
  expect(screen.getByRole("button", { name: "Убрать Новый Участник" })).toBeTruthy();
  expect(status()).toContain("создан");
});

it.each(["executors", "responsible_executors", "signers", "approvers"])("allows explicit creation for the %s scoped book", async code => {
  render(<Editor references={[{ ...roleBooks[0], code }]} onCreateReference={vi.fn()} />);
  fireEvent.change(await openLookup(), { target: { value: "Новое имя" } });
  expect(screen.getByRole("button", { name: "Создать «Новое имя»" })).toBeTruthy();
});

it.each([
  { onCreateReference: undefined },
  { references: [{ ...roleBooks[0], code: "departments" }] },
  { references: [{ ...roleBooks[0], registry_id: null }] },
  { definition: { ...field, type: "user" as const } },
  { definition: { key: "rows", label: "Строки", type: "group" as const, children: [field] }, initial: { rows: [{ custom_people: [] }] } },
])("keeps creation restricted to authorized top-level participant books: %j", async props => {
  if ("definition" in props && props.definition?.type === "user") fetchMock.mockImplementation(async () => new Response(JSON.stringify(people.map(p => ({ ...p, display_name: p.label })))));
  render(<Editor onCreateReference={vi.fn()} {...props} />);
  fireEvent.change(await openLookup(), { target: { value: "Новое имя" } });
  expect(screen.queryByRole("button", { name: /Создать/ })).toBeNull();
});

it("prevents duplicate creation for normalized active and inactive participants with business metadata", async () => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify([{ id: "inactive", label: "Анна Иванова", active: false, metadata: { position: "Юрист" } }])));
  render(<Editor onCreateReference={vi.fn()} />);
  fireEvent.change(await openLookup(), { target: { value: "  аННА   иВАНОВА  " } });
  expect(screen.queryByRole("button", { name: /Создать/ })).toBeNull();
  expect(status()).toContain("неактивен");
});

it("preserves the draft after creation failure, permits retry, and deduplicates reused participants", async () => {
  const create = vi.fn<CreateReference>().mockRejectedValueOnce(new Error("Нет права добавлять участника"))
    .mockResolvedValue({ item: people[0], created: false });
  render(<Editor initial={{ custom_people: ["anna"] }} onCreateReference={create} />);
  fireEvent.change(await openLookup(), { target: { value: "Новое имя" } });
  fireEvent.click(screen.getByRole("button", { name: /Создать/ }));
  expect((await screen.findByRole("alert")).textContent).toContain("Нет права");
  expect(values()).toBe('{"custom_people":["anna"]}');
  fireEvent.click(screen.getByRole("button", { name: /Создать/ }));
  await waitFor(() => expect(status()).toContain("выбран"));
  expect(values()).toBe('{"custom_people":["anna"]}');
  expect(screen.getAllByRole("button", { name: "Убрать Анна Иванова" })).toHaveLength(1);
});

it("blocks explicit creation at the selection limit", async () => {
  const create = vi.fn<CreateReference>();
  render(<Editor definition={{ ...field, constraints: { max_items: 1 } }} initial={{ custom_people: ["anna"] }} onCreateReference={create} />);
  fireEvent.change(await openLookup(), { target: { value: "Новое имя" } });
  expect(screen.getByRole<HTMLButtonElement>("button", { name: /Создать/ }).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /Создать/ }));
  expect(create).not.toHaveBeenCalled();
});

it.each(["registry", "mode"])("locks controls during creation and ignores a late response after %s changes", async change => {
  let resolve!: (value: { item: ReferenceItem; created: boolean }) => void;
  const create = vi.fn<CreateReference>(() => new Promise(done => { resolve = done; }));
  const { rerender } = render(<Editor onCreateReference={create} initial={{ custom_people: ["anna"] }} />);
  fireEvent.change(await openLookup(), { target: { value: "Новое имя" } });
  fireEvent.click(screen.getByRole("button", { name: /Создать/ }));
  expect(screen.getByRole<HTMLInputElement>("combobox").disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Убрать Анна Иванова" }).disabled).toBe(true);
  rerender(<Editor onCreateReference={create} initial={{ custom_people: ["anna"] }} registryId={change === "registry" ? "registry-b" : "registry-a"} mode={change === "mode" ? "view" : "edit"} />);
  await act(async () => resolve({ item: { id: "late", label: "Чужой участник", active: true }, created: true }));
  expect(values()).toBe('{"custom_people":["anna"]}');
  expect(screen.queryByText("Чужой участник")).toBeNull();
});

it("refreshes selectability when a mounted readonly card enters edit mode", async () => {
  const initial = { custom_people: ["anna"] };
  const { rerender } = render(<Editor mode="view" initial={initial} />);
  expect(await screen.findByText("Анна Иванова")).toBeTruthy();
  fetchMock.mockImplementation(async () => new Response(JSON.stringify(people.map(person => ({ ...person, selectable: person.id === "vera" })))));
  rerender(<Editor mode="edit" initial={initial} />);
  const input = await openLookup();
  expect(screen.getByRole("option", { name: "Борис Петров (неактивно)" }).getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Убрать Анна Иванова" }));
  fireEvent.change(input, { target: { value: "Анна" } });
  expect(values()).toBe('{"custom_people":[]}');
  fireEvent.change(input, { target: { value: "ВЕРА" } });
  expect(values()).toBe('{"custom_people":["vera"]}');
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("searches documents remotely and cancels stale responses", async () => {
  let resolve!: (value: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise(done => { resolve = done; }))
    .mockImplementation(async () => new Response(JSON.stringify({ items: [{ id: "doc-new", title: "Документ" }] })));
  render(<Editor definition={{ ...field, type: "document" }} />);
  const input = screen.getByRole("combobox");
  fireEvent.change(input, { target: { value: "акт" } });
  await screen.findByRole("option", { name: "Документ" });
  await act(async () => resolve(new Response(JSON.stringify({ items: [{ id: "doc-old", title: "Поздний ответ" }] }))));
  expect(screen.queryByRole("option", { name: "Поздний ответ" })).toBeNull();
  expect(String(fetchMock.mock.calls[1][0])).toContain("q=%D0%B0%D0%BA%D1%82");
  expect((fetchMock.mock.calls[0][1]?.signal as AbortSignal).aborted).toBe(true);
});

it("announces loading, empty dictionaries and request failures without allowing selections", async () => {
  let resolve!: (value: Response) => void;
  fetchMock.mockImplementation(() => new Promise(done => { resolve = done; }));
  render(<Editor />);
  expect(status()).toContain("Загрузка");
  await act(async () => resolve(new Response("[]")));
  expect(status()).toContain("Нет доступных значений");
  cleanup();
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ error: { message: "Доступ запрещён" } }), { status: 403 }));
  render(<Editor />);
  expect((await screen.findByRole("alert")).textContent).toContain("Доступ запрещён");
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "Анна" } });
  expect(values()).toBe("{}");
});

it.each([
  { mode: "view" as const, definition: field },
  { mode: "edit" as const, definition: { ...field, editable: false } },
  { mode: "edit" as const, definition: { ...field, write_roles: ["admin"] } },
])("keeps readonly references free from mutation controls: %j", async props => {
  render(<Editor {...props} initial={{ custom_people: ["anna"] }} onCreateReference={vi.fn()} />);
  expect(await screen.findByText("Анна Иванова")).toBeTruthy();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});

it("does not fetch or render hidden reference fields", () => {
  render(<Editor definition={{ ...field, read_roles: ["admin"] }} />);
  expect(screen.queryByText("Участники")).toBeNull();
  expect(fetchMock).not.toHaveBeenCalled();
});

it("resolves the current registry before global or foreign books and ignores stale scoped results", async () => {
  let resolve!: (value: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise(done => { resolve = done; }))
    .mockImplementation(async () => new Response(JSON.stringify([{ id: "person-b", label: "Исполнитель Б", active: true }])));
  const definition = { ...field, reference_set_id: undefined, reference_code: "executors" };
  const references = [{ ...roleBooks[0], id: "people-b", registry_id: "registry-b" },
    { ...roleBooks[0], id: "people-global", registry_id: null }, { ...roleBooks[0], id: "people-a" }];
  const { rerender } = render(<Editor definition={definition} references={references} />);
  rerender(<Editor definition={definition} registryId="registry-b" references={references} />);
  await openLookup();
  expect(screen.getByRole("option", { name: "Исполнитель Б" })).toBeTruthy();
  await act(async () => resolve(new Response(JSON.stringify([{ id: "person-a", label: "Исполнитель А", active: true }]))));
  expect(screen.queryByRole("option", { name: "Исполнитель А" })).toBeNull();
  expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
    expect.stringContaining("/references/people-a/items"), expect.stringContaining("/references/people-b/items"),
  ]);
});

it("searches approved participant metadata without exposing source attributes", async () => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify([
    { id: "one", label: "Анна Иванова", active: true, metadata: { position: "Юрист", department: "Правовой отдел", source_attributes: { secret: "private" } } },
    { id: "two", label: "Анна Иванова", active: true, metadata: { department: "Канцелярия" } },
  ])));
  render(<Editor />);
  fireEvent.change(await openLookup(), { target: { value: "правовой" } });
  expect(values()).toBe('{"custom_people":["one"]}');
  expect(screen.getByRole("button", { name: "Убрать Анна Иванова · Юрист · Правовой отдел" })).toBeTruthy();
  expect(screen.queryByText(/private/)).toBeNull();
});

it("does not fetch an explicit foreign reference set", async () => {
  render(<Editor references={[{ ...roleBooks[0], registry_id: "registry-b" }]} />);
  await waitFor(() => expect(status()).toContain("Нет доступных"));
  expect(fetchMock).not.toHaveBeenCalled();
});

it("renders a provided top-level field control while preserving read visibility and nested controls", () => {
  const custom: Field = { key: "number", label: "Номер", type: "text" };
  const schema = { sections: [{ key: "main", title: "", fields: [custom, { ...custom, key: "hidden", read_roles: ["admin"] },
    { key: "rows", label: "Строки", type: "group" as const, children: [custom] }] }] };
  render(<DynamicForm schema={schema} values={{ rows: [{ number: "nested" }] }} mode="edit" roles={["editor"]}
    fieldContent={{ number: <button type="button">Выбрать номер</button>, hidden: <button>Скрытый</button> }} />);
  expect(screen.getAllByRole("button", { name: "Выбрать номер" })).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Скрытый" })).toBeNull();
  expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Номер" }).value).toBe("nested");
});

it("never selects inactive dictionary records even if a conflicting selectable flag is true", async () => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify([{ id: "inactive", label: "Архивный участник", active: false, selectable: true }])));
  render(<Editor />);
  const input = await openLookup();
  fireEvent.change(input, { target: { value: "Архивный" } });
  expect(values()).toBe("{}");
  expect(screen.getByRole("option", { name: "Архивный участник (неактивно)" }).getAttribute("aria-disabled")).toBe("true");
});

const availableBooks: ReferenceSet[] = [
  { id: "people-a", code: "executors", name: "А", registry_id: "registry-a" },
  { id: "people-b", code: "executors", name: "Б", registry_id: "registry-b" },
  { id: "foreign-code", code: "departments", name: "Отделы", registry_id: "registry-b" },
];
const codeField = { ...field, reference_set_id: undefined, reference_code: "executors" };
it("aggregates all available authorized books by UUID without merging equal labels", async () => {
  fetchMock.mockImplementation(async url => new Response(JSON.stringify([
    { id: String(url).includes("people-a") ? "person-a" : "person-b", label: "Анна Иванова", active: true, metadata: { position: "Юрист" } },
    ...(String(url).includes("people-b") ? [{ id: "inactive-b", label: "Архивный участник", active: false }] : []),
  ])));
  render(<Editor definition={codeField} references={availableBooks} registryId="" referenceScope="available" initial={{ custom_people: ["inactive-b"] }} />);
  const input = await openLookup();
  fireEvent.change(input, { target: { value: "Анна" } });
  expect(values()).toBe('{"custom_people":["inactive-b"]}');
  const equalLabels = screen.getAllByRole("option", { name: /^Анна Иванова · Юрист · [АБ]$/ });
  expect(equalLabels).toHaveLength(2);
  expect(equalLabels.map(option => option.textContent)).toEqual(["Анна Иванова · Юрист · А", "Анна Иванова · Юрист · Б"]);
  fireEvent.click(equalLabels[0]);
  expect(values()).toBe('{"custom_people":["inactive-b","person-a"]}');
  fireEvent.click(screen.getByRole("option", { name: "Анна Иванова · Юрист · Б" }));
  expect(values()).toBe('{"custom_people":["inactive-b","person-a","person-b"]}');
  fireEvent.click(screen.getByRole("button", { name: "Убрать Архивный участник · Б" }));
  expect(values()).toBe('{"custom_people":["person-a","person-b"]}');
  expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
    expect.stringContaining("/references/people-a/items"), expect.stringContaining("/references/people-b/items"),
  ]);
});

it.each([undefined, "available"] as const)("keeps registry-scoped card isolation with referenceScope=%s", async referenceScope => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify([{ id: "person-a", label: "Участник А", active: true }])));
  render(<Editor definition={codeField} references={availableBooks} registryId="registry-a" referenceScope={referenceScope} />);
  await openLookup();
  expect(screen.getByRole("option", { name: "Участник А" })).toBeTruthy();
  expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([expect.stringContaining("/references/people-a/items")]);
});

it("fails closed on an available-book error and never auto-selects a partial unique response", async () => {
  let failBook!: (response: Response) => void;
  fetchMock.mockImplementation(async url => String(url).includes("people-a")
    ? new Response(JSON.stringify([{ id: "person-a", label: "Анна Иванова", active: true }]))
    : new Promise<Response>(resolve => { failBook = resolve; }));
  render(<Editor definition={codeField} references={availableBooks} registryId="" referenceScope="available" />);
  const input = screen.getByRole("combobox");
  fireEvent.focus(input);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  fireEvent.change(input, { target: { value: "Анна" } });
  expect(values()).toBe("{}");
  expect(screen.queryByRole("option")).toBeNull();
  await act(async () => failBook(new Response(JSON.stringify({ error: { message: "Справочник недоступен" } }), { status: 403 })));
  expect((await screen.findByRole("alert")).textContent).toContain("Справочник недоступен");
  expect(values()).toBe("{}");
  expect(screen.queryByRole("option")).toBeNull();
});

it("aborts all available-book requests when scope changes and ignores late aggregate results", async () => {
  const pending: ((response: Response) => void)[] = [];
  fetchMock.mockImplementation(() => new Promise<Response>(resolve => { pending.push(resolve); }));
  const { rerender } = render(<Editor definition={codeField} references={availableBooks} registryId="" referenceScope="available" />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const aggregateSignals = fetchMock.mock.calls.map(([, init]) => init?.signal as AbortSignal);
  rerender(<Editor definition={codeField} references={availableBooks} registryId="registry-a" referenceScope="available" />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  expect(aggregateSignals.every(signal => signal.aborted)).toBe(true);
  fireEvent.focus(screen.getByRole("combobox"));
  await act(async () => pending[2](new Response(JSON.stringify([{ id: "current", label: "Текущий участник", active: true }]))));
  await screen.findByRole("option", { name: "Текущий участник" });
  await act(async () => {
    pending[0](new Response(JSON.stringify([{ id: "late-a", label: "Поздний А", active: true }])));
    pending[1](new Response(JSON.stringify([{ id: "late-b", label: "Поздний Б", active: true }])));
  });
  expect(screen.queryByRole("option", { name: /Поздний/ })).toBeNull();
  expect(screen.getByRole("option", { name: "Текущий участник" })).toBeTruthy();
});

it("loads an authorized explicit registry-local book in available all-registry filters while preserving card isolation", async () => {
  const explicit = { ...field, reference_set_id: "people-b" };
  const { rerender } = render(<Editor definition={explicit} references={availableBooks} registryId="" referenceScope="available" />);
  const input = await openLookup();
  expect(screen.getByRole("option", { name: "Анна Иванова" })).toBeTruthy();
  fireEvent.change(input, { target: { value: "Анна" } });
  expect(values()).toBe('{"custom_people":["anna"]}');
  expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([expect.stringContaining("/references/people-b/items")]);
  rerender(<Editor definition={explicit} references={availableBooks} registryId="registry-a" />);
  await waitFor(() => expect(status()).toContain("Нет доступных"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("does not load an explicit book absent from authorized available references", async () => {
  render(<Editor definition={{ ...field, reference_set_id: "unlisted" }} references={availableBooks} registryId="" referenceScope="available" />);
  await waitFor(() => expect(status()).toContain("Нет доступных"));
  expect(fetchMock).not.toHaveBeenCalled();
});
