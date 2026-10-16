import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { Documents } from "./Documents";
import type { FormSchema } from "../engine/schema";

afterEach(() => {cleanup(); vi.unstubAllGlobals(); localStorage.clear(); window.history.replaceState(null, "", "/work");});
function setup(mixedVersions = false, sectionNavigation = false, role = "editor", create = false) {
  let revision = 1;
  const schema: FormSchema = {sections: [{key: "main", title: "Основное", fields: [
    {key: "caption", label: "Наименование", type: "text", system: "title", editable: true},
    {key: "x_kind", label: "Категория", type: "text", filterable: true},
    {key: "x_topic", label: "Содержание", type: "text", filterable: true},
    {key: "x_owner", label: "Куратор", type: "user"},
    {key: "private", label: "Закрыто", type: "text", read_roles: ["admin"]},
  ]}], list_view: {columns: [{key: "x_kind", label: "Вид документа"}, {key: "x_topic", label: "Тема"}, {key: "x_owner", label: "Ответственный исполнитель"}]}};
  if (sectionNavigation) schema.sections.push(
    {key: "publication", title: "Публикация", fields: [{key: "published", label: "Место публикации", type: "text", editable: true}]},
    {key: "control", title: "Контроль исполнения", fields: [{key: "result", label: "Результат", type: "text", editable: true}]},
    {key: "restricted", title: "Закрытый раздел", fields: [{key: "secret", label: "Скрытое поле", type: "text", read_roles: ["admin"]}]},
  );
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "layout-user", roles: [role], csrf_token: "csrf"};
    else if (url.endsWith("/registries")) data = [{id: "r", name: "Реестр", document_type_ids: ["type"], permissions: ["read", "update", ...(create ? ["create"] : [])]}];
    else if (url.includes("/document-types/type/versions")) {
      const newer = JSON.parse(JSON.stringify(schema));
      newer.sections[0].fields.find((field: {key: string}) => field.key === "x_topic").key = "new_topic";
      newer.list_view.columns.find((column: {key: string}) => column.key === "x_topic").key = "new_topic";
      data = [{id: "schema", state: "published", document_type_id: "type", version: 1, schema},
        ...(mixedVersions ? [{id: "schema2", state: "published", document_type_id: "type", version: 2, schema: newer}] : [])];
    }
    else if (url.endsWith("/document-types")) data = [{id: "type", name: "Приказ"}];
    else if (url.endsWith("/search")) data = {items: ["a", "b"].map((id, index) => ({document: {id, document_type_id: "type", schema_version_id: "schema", title: index ? "Бета" : "Альфа", card_state: index ? "published" : "draft", reg_number: "Р-" + (index + 1), reg_date: "2026-10-16", values: {caption: "Альфа", x_kind: "Распоряжение", x_topic: "Благоустройство", x_owner: "opaque-id"}, display_values: {x_kind: "Распоряжение", x_topic: "Благоустройство", x_owner: "Сотрудник"}}, matches: []})), total: 40, next_cursor: "page-two", page_size: 20};
    else if (url.endsWith("/documents") && init?.method === "POST") data = {id: "a"};
    else if (url.includes("/documents/a/content")) data = {items: [], total: 0, page: 1, page_size: 20};
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

it("keeps the four working tabs and permission-aware section navigation without duplicate header metadata", async () => {
  setup(false, true);
  fireEvent.click(await firstRow());
  await screen.findByRole("button", {name: "Редактировать карточку"});
  const card = document.querySelector(".document-card") as HTMLElement;
  expect(Array.from(card.querySelectorAll(":scope > .tabs button"), item => item.textContent)).toEqual(["Карточка", "Файлы и версии", "История изменений", "Содержание"]);
  const header = card.querySelector(":scope > .toolbar")!;
  expect(header.textContent).not.toContain("UUID:");
  expect(header.textContent).not.toContain("редакция");
  expect(header.textContent).not.toContain("Р-1");
  const nav = within(card).getByRole("navigation", {name: "Разделы формы"});
  expect(within(nav).getAllByRole("link").map(link => link.textContent)).toEqual(["Основное", "Публикация", "Контроль исполнения"]);
  for (const link of within(nav).getAllByRole("link")) expect(document.getElementById(link.getAttribute("href")!.slice(1))).toBeTruthy();
  expect(within(card).queryByText("Скрытое поле")).toBeNull();
  expect(within(card).getByText("Альфа", {selector: ".field-value"})).toBeTruthy();
  fireEvent.click(within(card).getByRole("button", {name: "Редактировать карточку"}));
  const input = screen.getByLabelText("Наименование");
  fireEvent.change(input, {target: {value: "Черновик раздела"}});
  window.history.pushState(null, "", window.location.pathname + window.location.search + nav.querySelector("a")!.getAttribute("href"));
  fireEvent(window, new PopStateEvent("popstate"));
  expect(screen.getByLabelText("Наименование")).toBe(input);
  expect((input as HTMLInputElement).value).toBe("Черновик раздела");
});

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
it("keeps one mounted card and its draft, active tab, selection, query and loaded results across modes", async () => {
  const fetch = setup();
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  fireEvent.change(screen.getByLabelText("Наименование"), {target: {value: "Черновик"}});
  fireEvent.change(screen.getByRole("searchbox", {name: /Поиск/}), {target: {value: "Запрос"}});
  fireEvent.scroll(screen.getByRole("region", {name: "Список документов"}));
  const requests = fetch.mock.calls.filter(([url]) => url.includes("/documents/a?")).length;
  for (const name of ["Таблица", "Карточка", "Вместе"]) fireEvent.click(mode(name));
  expect((screen.getByLabelText("Наименование") as HTMLInputElement).value).toBe("Черновик");
  expect((screen.getByRole("searchbox", {name: /Поиск/}) as HTMLInputElement).value).toBe("Запрос");
  expect(screen.queryByText(/Страница/)).toBeNull();
  expect(fetch.mock.calls.filter(([url]) => url.includes("/documents/a?")).length).toBe(requests);
  fireEvent.click(screen.getByRole("button", {name: "Файлы и версии"}));
  fireEvent.click(mode("Карточка")); fireEvent.click(mode("Вместе"));
  expect(screen.getByRole("button", {name: "Файлы и версии"}).className).toContain("active");
});
it("renders eight columns from each document's configured schema and sorts via the existing API", async () => {
  const fetch = setup();
  fireEvent.click(await screen.findByRole("radio", {name: "Таблица"}));
  await screen.findAllByText("Благоустройство");
  expect(screen.getAllByRole("columnheader")).toHaveLength(8);
  expect(screen.getByRole("columnheader", {name: "Состояние карточки"})).toBeTruthy();
  expect(screen.queryByRole("button", {name: "Сортировать: Состояние карточки"})).toBeNull();
  expect(within(await firstRow()).getByRole("gridcell", {name: "Состояние карточки: Черновик"})).toBeTruthy();
  const row = within(await firstRow());
  expect(row.getByText("Сотрудник")).toBeTruthy();
  expect(row.queryByText("opaque-id")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "Сортировать: Наименование документа"}));
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).some(([, init]) => JSON.parse(String(init?.body)).sort === "title")).toBe(true));
  expect(localStorage.getItem("acti.documents.layout.v1.layout-user.editor")).toContain("table");
});

it("does not offer a page-dependent custom sort when another published version uses a different field", async () => {
  window.history.replaceState(null, "", "/work?document_type_id=type");
  const fetch = setup(true);
  fireEvent.click(await screen.findByRole("radio", {name: "Таблица"}));
  await screen.findAllByText("Благоустройство");
  expect(screen.queryByRole("button", {name: "Сортировать: Тема"})).toBeNull();
  expect(screen.getByRole("button", {name: "Сортировать: Вид документа"})).toBeTruthy();
  fireEvent.scroll(screen.getByRole("region", {name: "Список документов"}));
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).length).toBeGreaterThan(1));
  expect(screen.queryByRole("button", {name: "Сортировать: Тема"})).toBeNull();
});

it("groups export inside collapsible search and places compact view controls directly above the panes", async () => {
  setup();
  await firstRow();
  const searchPanel = screen.getByRole("group", {name: "Поиск документов"});
  const toolbar = screen.getByRole("group", {name: "Вид и сортировка документов"});
  expect(within(toolbar).getByRole("radiogroup", {name: "Отображение рабочей области"})).toBeTruthy();
  expect(within(toolbar).getByLabelText("Сортировка")).toBeTruthy();
  expect(searchPanel.contains(toolbar)).toBe(false);
  fireEvent.click(within(searchPanel).getByText("Экспорт и импорт", {exact: true}));
  expect(within(searchPanel).getByRole("button", {name: "Экспорт CSV"})).toBeTruthy();
  const query = screen.getByRole("searchbox");
  fireEvent.change(query, {target: {value: "Черновик запроса"}});
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  const cardInput = screen.getByLabelText("Наименование");
  fireEvent.change(cardInput, {target: {value: "Черновик карточки"}});
  const toggle = within(searchPanel).getByText("Поиск", {exact: true, selector: "summary > span:first-child"});
  fireEvent.click(toggle);
  await waitFor(() => expect((searchPanel as HTMLDetailsElement).open).toBe(false));
  await waitFor(() => expect(screen.queryByRole("searchbox")).toBeNull());
  expect((screen.getByLabelText("Наименование") as HTMLInputElement).value).toBe("Черновик карточки");
  fireEvent.click(toggle);
  await waitFor(() => expect((searchPanel as HTMLDetailsElement).open).toBe(true));
  expect(screen.getByRole("searchbox")).toBe(query);
  expect((query as HTMLInputElement).value).toBe("Черновик запроса");
  expect(screen.getByLabelText("Наименование")).toBe(cardInput);
});

it("searches all permitted registries without asking the user to pick just one", async () => {
  const fetch = setup();
  await firstRow();
  expect(screen.queryByText("Выберите реестр для просмотра документов.")).toBeNull();
  expect(screen.getByText("По всем доступным реестрам")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Реестр", {exact: true}), {target: {value: "r"}});
  fireEvent.change(screen.getByLabelText("Реестр", {exact: true}), {target: {value: ""}});
  fireEvent.change(screen.getByRole("searchbox"), {target: {value: "Общий запрос"}});
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).some(([, init]) => {
    const body = JSON.parse(String(init?.body)); return body.query === "Общий запрос" && body.registry_ids.length === 0;
  })).toBe(true));
  const row = within(await firstRow());
  expect(row.getByRole("gridcell", {name: "Наименование документа: Альфа"})).toBeTruthy();
  expect(row.getByRole("gridcell", {name: "Регистрационный номер: Р-1"})).toBeTruthy();
  expect(row.getByRole("gridcell", {name: "Дата регистрации: 2026-10-16"})).toBeTruthy();
});

it("places saved filters below search and creation beside the view switch with All registries selected", async () => {
  const fetch = setup(false, false, "editor", true);
  await firstRow();
  const search = document.querySelector(".search-form")!;
  const views = document.querySelector(".search-saved-views")!;
  expect(search.compareDocumentPosition(views) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const toolbar = screen.getByRole("group", {name: "Вид и сортировка документов"});
  expect(within(toolbar).getByLabelText("Реестр")).toHaveProperty("value", "");
  fireEvent.click(mode("Таблица"));
  fireEvent.click(within(toolbar).getByRole("button", {name: "Создать документ"}));
  expect(screen.getByLabelText("Реестр документа")).toHaveProperty("value", "r");
  fireEvent.change(screen.getByLabelText("Тип документа"), {target: {value: "type"}});
  const creation = document.querySelector(".document-create") as HTMLElement;
  const form = within(creation);
  const create = await form.findByRole("button", {name: "Создать документ"});
  expect(form.getByLabelText("Состояние карточки")).toHaveProperty("value", "draft");
  expect(form.getByLabelText("Внутренняя база")).toHaveProperty("checked", true);
  expect(form.getByLabelText("Общая база")).toHaveProperty("checked", false);
  fireEvent.change(form.getByLabelText("Состояние карточки"), {target: {value: "published"}});
  fireEvent.click(form.getByLabelText("Общая база"));
  fireEvent.click(create);
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/documents") && init?.method === "POST" && JSON.parse(String(init.body)).card_state === "published" && JSON.parse(String(init.body)).publication_targets.join(",") === "internal,shared")).toBe(true));
  await waitFor(() => expect(mode("Вместе").getAttribute("aria-checked")).toBe("true"));
  fireEvent.click(await screen.findByRole("button", {name: "К списку"}));
  expect(mode("Таблица").getAttribute("aria-checked")).toBe("true");
});

it("keeps lifecycle in Main information and targets in the existing Publication section", async () => {
  const fetch = setup(false, true);
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  const card = document.querySelector(".document-card") as HTMLElement;
  const publication = within(card).getByRole("heading", {name: "Публикация"}).closest("section")!;
  expect(within(publication).getByLabelText("Место публикации")).toBeTruthy();
  const main = within(card).getByRole("heading", {name: "Основное"}).closest("section")!;
  expect(within(publication).queryByLabelText("Состояние карточки")).toBeNull();
  fireEvent.change(within(main).getByLabelText("Состояние карточки"), {target: {value: "published"}});
  fireEvent.click(within(publication).getByLabelText("Общая база"));
  fireEvent.click(screen.getByRole("button", {name: "Сохранить карточку"}));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT" && JSON.parse(String(init.body)).card_state === "published" && JSON.parse(String(init.body)).publication_targets.includes("shared"))).toBe(true));
});

it("treats user as a reader even when document grants include update and create", async () => {
  const fetch = setup(false, false, "user", true);
  fireEvent.click(await firstRow());
  await screen.findByRole("button", {name: "Содержание"});
  expect(screen.queryByRole("button", {name: "Редактировать карточку"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Создать документ"})).toBeNull();
  expect(screen.queryByRole("option", {name: "Черновик"})).toBeNull();
  expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/search") && JSON.parse(String(init?.body)).mode === "viewer" && JSON.parse(String(init?.body)).card_state === "published")).toBe(true);
});

it("creates inside the card pane, guards dirty navigation and restores the mounted selected draft on cancel", async () => {
  const fetch = setup(false, true, "editor", true);
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  const previousInput = screen.getByLabelText("Наименование") as HTMLInputElement;
  fireEvent.change(previousInput, {target: {value: "Черновик выбранной карточки"}});
  const query = screen.getByRole("searchbox");
  fireEvent.change(query, {target: {value: "Несохранённый запрос"}});
  const pane = screen.getByRole("region", {name: "Список документов"});
  pane.scrollTop = 85;
  fireEvent.click(mode("Таблица"));
  const toolbar = screen.getByRole("group", {name: "Вид и сортировка документов"});
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(within(toolbar).getByRole("button", {name: "Создать документ"}));
  expect(document.querySelector(".document-create")).toBeNull();
  confirm.mockReturnValue(true);
  const requests = fetch.mock.calls.filter(([url]) => url.endsWith("/search")).length;
  fireEvent.click(within(toolbar).getByRole("button", {name: "Создать документ"}));
  expect(mode("Вместе")).toHaveProperty("ariaChecked", "true");
  expect(screen.getByRole("searchbox")).toBe(query);
  expect(screen.getByRole("region", {name: "Список документов"})).toBe(pane);
  expect(previousInput.isConnected).toBe(true);
  const creation = document.querySelector(".document-create") as HTMLElement;
  expect(screen.getByRole("region", {name: "Содержимое карточки"}).contains(creation)).toBe(true);
  fireEvent.change(within(creation).getByLabelText("Тип документа"), {target: {value: "type"}});
  const newInput = await within(creation).findByLabelText("Наименование");
  fireEvent.change(newInput, {target: {value: "Новая несохранённая карточка"}});
  for (const name of ["Таблица", "Карточка", "Вместе"]) fireEvent.click(mode(name));
  expect(within(creation).getByLabelText("Наименование")).toBe(newInput);
  confirm.mockReturnValue(false);
  fireEvent.click(screen.getByRole("row", {name: /Выбрать документ «Бета»/}));
  expect(document.querySelector(".document-create")).toBe(creation);
  expect(window.dispatchEvent(new Event("acti:before-workspace-navigation", {cancelable: true}))).toBe(false);
  expect(window.dispatchEvent(new Event("beforeunload", {cancelable: true}))).toBe(false);
  fireEvent.click(within(creation).getByRole("button", {name: "Отмена создания"}));
  expect(document.querySelector(".document-create")).toBe(creation);
  confirm.mockReturnValue(true);
  fireEvent.click(within(creation).getByRole("button", {name: "Отмена создания"}));
  expect(document.querySelector(".document-create")).toBeNull();
  expect(mode("Таблица")).toHaveProperty("ariaChecked", "true");
  expect(previousInput.isConnected).toBe(true);
  expect(previousInput.value).toBe("Черновик выбранной карточки");
  expect((query as HTMLInputElement).value).toBe("Несохранённый запрос");
  expect(pane.scrollTop).toBe(85);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith("/search")).length).toBe(requests);
});

it.each(["row", "create"])("guards the retained selected draft before replacing it through %s from pristine creation", async operation => {
  const fetch = setup(false, true, "editor", true);
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  const previousInput = screen.getByLabelText("Наименование") as HTMLInputElement;
  fireEvent.change(previousInput, {target: {value: "Несохранённая выбранная карточка"}});
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(within(screen.getByRole("group", {name: "Вид и сортировка документов"})).getByRole("button", {name: "Создать документ"}));
  const creation = document.querySelector(".document-create") as HTMLElement;
  fireEvent.change(within(creation).getByLabelText("Тип документа"), {target: {value: "type"}});
  await within(creation).findByLabelText("Наименование");
  confirm.mockClear(); confirm.mockReturnValue(false);
  if (operation === "row") fireEvent.click(screen.getByRole("row", {name: /Выбрать документ «Бета»/}));
  else fireEvent.click(within(creation).getByRole("button", {name: "Создать документ"}));
  expect(confirm).toHaveBeenCalledOnce();
  expect(document.querySelector(".document-create")).toBe(creation);
  expect(previousInput.isConnected).toBe(true);
  expect(previousInput.value).toBe("Несохранённая выбранная карточка");
  expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/documents") && init?.method === "POST")).toBe(false);
  expect(fetch.mock.calls.some(([url]) => url.includes("/documents/b?"))).toBe(false);
  fireEvent.click(within(creation).getByRole("button", {name: "Отмена создания"}));
  expect(previousInput.isConnected).toBe(true);
  expect(screen.getByLabelText("Наименование")).toBe(previousInput);
});

it.each([false, true])("restores URL and mounted draft when history replacement is cancelled (creation=%s)", async creating => {
  setup(false, true, "editor", true);
  fireEvent.click(await firstRow());
  fireEvent.click(await screen.findByRole("button", {name: "Редактировать карточку"}));
  let form = document.querySelector(".document-card") as HTMLElement;
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  if (creating) {
    fireEvent.click(within(screen.getByRole("group", {name: "Вид и сортировка документов"})).getByRole("button", {name: "Создать документ"}));
    form = document.querySelector(".document-create") as HTMLElement;
    fireEvent.change(within(form).getByLabelText("Тип документа"), {target: {value: "type"}});
    await within(form).findByLabelText("Наименование");
  }
  const input = within(form).getByLabelText("Наименование") as HTMLInputElement;
  fireEvent.change(input, {target: {value: "Черновик для истории"}});
  const original = window.location.pathname + window.location.search;
  confirm.mockClear(); confirm.mockReturnValue(false);
  const anchor = within(form).getByRole("navigation", {name: "Разделы формы"}).querySelector("a")!.getAttribute("href")!;
  window.history.pushState(null, "", original + anchor);
  fireEvent(window, new PopStateEvent("popstate"));
  expect(confirm).not.toHaveBeenCalled();
  expect(within(form).getByLabelText("Наименование")).toBe(input);
  const prior = window.location.pathname + window.location.search + window.location.hash;
  window.history.pushState(null, "", "/work?document=b");
  fireEvent(window, new PopStateEvent("popstate"));
  expect(confirm).toHaveBeenCalledOnce();
  expect(window.location.pathname + window.location.search + window.location.hash).toBe(prior);
  expect(input.isConnected).toBe(true);
  expect(input.value).toBe("Черновик для истории");
});

it("restores the content tab on same-document history even without a file or block target", async () => {
  setup();
  fireEvent.click(await firstRow());
  await screen.findByRole("button", {name: "Редактировать карточку"});
  window.history.pushState(null, "", "/work?document=a&tab=content");
  fireEvent(window, new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.getByRole("button", {name: "Содержание"}).className).toContain("active"));
  window.history.pushState(null, "", "/work?document=a");
  fireEvent(window, new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.getByRole("button", {name: "Карточка"}).className).toContain("active"));
});
