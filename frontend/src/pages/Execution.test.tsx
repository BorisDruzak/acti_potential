import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { localDeadline, resultPayload, utcDeadline, type ProcessInstance } from "../engine/execution";
import { newProcessStep } from "../engine/processes";
import type { FormSchema } from "../engine/schema";
import { ExecutionDetail } from "./ExecutionDetail";
import { ExecutionStartForm } from "./ExecutionStart";
import { MyWork } from "./MyWork";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
const schema: FormSchema = {sections: [{key: "main", title: "Ответы шага", fields: [{key: "route", label: "Маршрут результата", type: "text", editable: true}, {key: "amount", label: "Сумма результата", type: "decimal", editable: true}]}]};
const step = {...newProcessStep(), key: "prepare", title: "Подготовить документ", instruction: "Проверьте материалы", is_start: true, form_schema_id: "form1", form_schema: schema};
const initial: ProcessInstance = {id: "instance", registry_id: "registry", process_version_id: "pv1", name: "Синтетическое дело", state: "active", revision: 1, created_by: "editor", created_by_name: "Сотрудник", assignee_id: "editor", assignee_name: "Сотрудник", current_step_key: "prepare", deadline_at: null, overdue: false, process_snapshot: {version: 1, metadata_snapshot: {name: "Подготовка документа"}, steps: [step], transitions: []}, steps: [{id: "attempt1", step_key: "prepare", attempt: 1, state: "active", assignee_id: "editor", assignee_name: "Сотрудник", overdue: false, checklist: [{id: "check1", label: "Материалы проверены", kind: "input", required: true, checked: false}], result: null}], permissions: {control: true, work: true}};
type Handler = (url: string, init?: RequestInit) => unknown;
function setup(element: React.ReactNode, {instance = initial, viewer = false, handler = (() => undefined) as Handler} = {}) {
  let current = structuredClone(instance);
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data = handler(url, init);
    if (data instanceof Response) return data;
    if (url.endsWith("/users/me")) data = {id: "editor", display_name: "Сотрудник", roles: [viewer ? "viewer" : "editor"], csrf_token: "csrf"};
    else if (data === undefined && url.includes("/execution/instances/instance/history")) data = {items: [], total: 0, page: 1, page_size: 20};
    else if (data === undefined && url.includes("/execution/instances/instance/steps/")) data = {items: []};
    else if (data === undefined && url.includes("/execution/instances/instance") && init?.method === "POST") {
      const body = JSON.parse(String(init.body)); current = {...current, revision: current.revision + 1};
      if (url.endsWith("/result")) current.steps[0].result = {id: "result1", values: body.values, comment: body.comment, attachments: body.attachments, created_at: "2026-01-01T00:00:00Z"};
      data = structuredClone(current);
    }
    else if (data === undefined && url.includes("/execution/instances/instance")) data = structuredClone(current);
    else if (data === undefined && url.includes("/execution/instances?")) data = {items: [current], total: 1, page: 1, page_size: 20};
    else if (data === undefined && url.includes("/execution/assignees")) data = [{id: "editor", display_name: "Сотрудник"}, {id: "another", display_name: "Другой сотрудник"}];
    else if (data === undefined && url.endsWith("/registries")) data = [{id: "registry", name: "Реестр дел", document_type_ids: [], permissions: ["read", "update"]}];
    return new Response(JSON.stringify(data === undefined ? [] : data));
  });
  vi.stubGlobal("fetch", fetch);
  function Ready() {return useAuth().user ? element : null;}
  render(<AuthProvider><Ready/></AuthProvider>);
  return fetch;
}

it("confirms typed answers and advances with the latest revision without choosing a client route", async () => {
  const fetch = setup(<ExecutionDetail id="instance" readOnly={false}/>);
  fireEvent.change(await screen.findByLabelText("Маршрут результата"), {target: {value: "standard"}});
  fireEvent.change(screen.getByLabelText("Сумма результата"), {target: {value: "123.45"}});
  fireEvent.change(screen.getByLabelText("Комментарий к результату"), {target: {value: "Проверенный результат"}});
  fireEvent.click(screen.getByRole("button", {name: "Подтвердить результат шага"}));
  await screen.findByRole("heading", {name: "Подтверждённый результат"});
  const confirmed = fetch.mock.calls.find(([url, init]) => url.endsWith("/result") && init?.method === "POST")!;
  expect(JSON.parse(String(confirmed[1]?.body))).toEqual({revision: 1, values: {route: "standard", amount: "123.45"}, comment: "Проверенный результат", attachments: []});
  fireEvent.click(screen.getByLabelText("Материалы проверены · обязательно"));
  fireEvent.click(screen.getByRole("button", {name: "Сохранить проверки"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith("/checklist"))).toBe(true));
  await waitFor(() => expect((screen.getByRole("button", {name: "Завершить шаг"}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", {name: "Завершить шаг"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith("/advance"))).toBe(true));
  const advance = fetch.mock.calls.find(([url]) => url.endsWith("/advance"))!;
  expect(JSON.parse(String(advance[1]?.body))).toEqual({revision: 3});
  expect(initial.process_snapshot.steps[0].form_schema).toEqual(schema);
});

it("uses a compatible ordinary document revision for managed/file results and sends no injected values", async () => {
  const fetch = setup(<ExecutionDetail id="instance" readOnly={false}/>, {handler: url => {
    if (url.includes("/documents?")) return {items: [{id: "wrong", title: "Другая форма"}, {id: "doc", title: "Документ результата"}]};
    if (url.includes("/documents/wrong?")) return {id: "wrong", schema_version_id: "different", revision: 2};
    if (url.includes("/documents/doc?")) return {id: "doc", schema_version_id: "form1", revision: 7, current_version: 3};
  }});
  fireEvent.change(await screen.findByLabelText("Способ заполнения результата"), {target: {value: "document"}});
  await screen.findByRole("option", {name: "Другая форма"});
  fireEvent.change(screen.getByLabelText("Карточка результата"), {target: {value: "wrong"}});
  await screen.findByText("Форма документа должна точно совпадать с закреплённой формой шага.");
  expect((screen.getByRole("button", {name: "Подтвердить результат шага"}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Карточка результата"), {target: {value: "doc"}});
  await screen.findByText(/Совместимая форма · редакция метаданных 7/);
  fireEvent.click(screen.getByRole("button", {name: "Подтвердить результат шага"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith("/result"))).toBe(true));
  const request = fetch.mock.calls.find(([url]) => url.endsWith("/result"))!;
  expect(JSON.parse(String(request[1]?.body))).toMatchObject({values: {}, result_document: {document_id: "doc", revision: 7}});
});

it("keeps reader execution and restricted bases read only without leaking extra placeholder content", async () => {
  const fetch = setup(<ExecutionDetail id="instance" readOnly/>, {viewer: true, handler: url => url.includes("/attempt1/basis?") ? {items: [{restricted: true, document: {id: "hidden-id", title: "Секретный акт"}, files: [{id: "private-file", name: "private.docx"}]}]} : undefined});
  fireEvent.click(await screen.findByRole("button", {name: "Основания шага дела"}));
  await screen.findByText("Документ ограничен");
  for (const hidden of ["hidden-id", "Секретный акт", "private.docx"]) expect(document.body.textContent).not.toContain(hidden);
  for (const action of ["Подтвердить результат шага", "Завершить шаг", "Назначить ответственного", "Отменить дело"]) expect(screen.queryByRole("button", {name: action})).toBeNull();
  expect((screen.getByLabelText("Материалы проверены · обязательно") as HTMLInputElement).disabled).toBe(true);
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it("requires a control reason and preserves deadline timezone conversion", async () => {
  const fetch = setup(<ExecutionDetail id="instance" readOnly={false}/>);
  fireEvent.click(await screen.findByRole("button", {name: "Изменить срок дела"}));
  fireEvent.change(screen.getByLabelText(/^Новый срок дела/), {target: {value: "2026-10-20T10:30"}});
  const confirm = screen.getByRole("button", {name: "Подтвердить действие"}) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Причина действия"), {target: {value: "Уточнён срок подготовки"}});
  fireEvent.click(confirm);
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith("/deadline"))).toBe(true));
  const request = fetch.mock.calls.find(([url]) => url.endsWith("/deadline"))!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({revision: 1, reason: "Уточнён срок подготовки", deadline_at: utcDeadline("2026-10-20T10:30")});
  expect(localDeadline(utcDeadline("2026-10-20T10:30"))).toBe("2026-10-20T10:30");
});

it("shows a server denied transition without selecting else or losing unsaved answers", async () => {
  const fetch = setup(<ExecutionDetail id="instance" readOnly={false}/>, {handler: url => url.endsWith("/advance") ? new Response(JSON.stringify({error: {code: "execution_route_restricted", message: "Недостаточно доступа к условиям маршрута"}}), {status: 403}) : undefined});
  fireEvent.change(await screen.findByLabelText("Маршрут результата"), {target: {value: "unsaved"}});
  fireEvent.click(screen.getByRole("button", {name: "Завершить шаг"}));
  await screen.findByRole("alert");
  expect(screen.getByRole("alert").textContent).toContain("Недостаточно доступа");
  expect((screen.getByLabelText("Маршрут результата") as HTMLInputElement).value).toBe("unsaved");
  expect(fetch.mock.calls.filter(([url]) => url.endsWith("/advance"))).toHaveLength(1);
  expect(fetch.mock.calls.some(([url]) => url.includes("/else") || url.includes("/result"))).toBe(false);
});

it("keeps later execution history pages reachable", async () => {
  const fetch = setup(<ExecutionDetail id="instance" readOnly/>, {viewer: true, handler: url => url.includes("/history?") ? {items: [{id: "event", action: "returned", actor_name: "Сотрудник", reason: "Исправление результата", created_at: "2026-01-01T10:00:00Z"}], total: 21, page: 1, page_size: 20} : undefined});
  await screen.findByText("Исправление результата");
  fireEvent.click(screen.getByRole("button", {name: "Следующие события дела"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("/history?") && url.includes("page=2"))).toBe(true));
});

it("starts a separate instance only after an explicit submit with the pinned process version", async () => {
  const created = vi.fn();
  const fetch = setup(<ExecutionStartForm versionId="pv1" registryId="registry" processName="Подготовка" onCreated={created} onClose={() => {}}/>, {handler: (url, init) => url.endsWith("/execution/instances") && init?.method === "POST" ? initial : undefined});
  await screen.findByRole("heading", {name: "Новое дело · Подготовка"});
  await screen.findByRole("option", {name: "Сотрудник"});
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  fireEvent.change(screen.getByLabelText("Название дела"), {target: {value: "Дело номер один"}});
  fireEvent.click(screen.getByRole("button", {name: "Создать дело"}));
  await waitFor(() => expect(created).toHaveBeenCalledWith(initial));
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/execution/instances") && init?.method === "POST")!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({process_version_id: "pv1", name: "Дело номер один", deadline_at: null});
});

it("filters editor work by assignment and overdue while reader sees authorized summaries only", async () => {
  const fetch = setup(<MyWork readOnly={false}/>);
  fireEvent.click(await screen.findByLabelText("Все доступные дела"));
  expect(screen.queryByRole("button", {name: "Начать исполнение процесса"})).toBeNull();
  fireEvent.click(screen.getByLabelText("Только просроченные дела"));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("mine=false") && url.includes("overdue=true"))).toBe(true));
  cleanup();
  setup(<MyWork readOnly/>, {viewer: true});
  await screen.findByRole("heading", {name: "Ход работы"});
  expect(screen.queryByRole("button", {name: "Начать исполнение процесса"})).toBeNull();
  expect(screen.queryByLabelText("Все доступные дела")).toBeNull();
});

it("serializes authorized result references only and refuses restricted attachment overwrites", () => {
  expect(resultPayload({route: "standard"}, "Готово", [{document_id: "doc", version_id: "v1", file_id: "file", note: "Оригинал", restricted: false}])).toEqual({values: {route: "standard"}, comment: "Готово", attachments: [{document_id: "doc", version_id: "v1", block_id: null, file_id: "file", note: "Оригинал"}]});
  expect(() => resultPayload({}, "", [{restricted: true}])).toThrow(/недоступные документы/);
});

it("shows only pinned result file IDs and resolves references in the authorized source registry", async () => {
  const fileSchema: FormSchema = {sections: [{key: "result", title: "Сохранённые сведения", fields: [{key: "title", label: "Заголовок результата", type: "text"}, {key: "original", label: "Оригинал результата", type: "file"}, {key: "department", label: "Отдел результата", type: "reference", reference_code: "departments"}]}]};
  const result = {id: "saved-result", values: {title: "Исторический заголовок", original: ["old-file"], department: "source-choice"}, comment: "Подтверждено", attachments: [], created_at: "2026-01-01T00:00:00Z", result_document: {document_id: "result-doc", revision: 1}};
  const instance: ProcessInstance = {...initial, state: "completed", process_snapshot: {...initial.process_snapshot, steps: [{...step, form_schema: fileSchema}]}, steps: [{...initial.steps[0], state: "completed", result}]};
  const fetch = setup(<ExecutionDetail id="instance" readOnly/>, {instance, viewer: true, handler: url => {
    if (url.endsWith("/references?mode=viewer")) return [{id: "instance-departments", code: "departments", registry_id: "registry"}, {id: "source-departments", code: "departments", registry_id: "source-registry"}];
    if (url.includes("/references/source-departments/items")) return [{id: "source-choice", label: "Отдел исходного реестра", active: true}];
    if (url.includes("/documents/result-doc?")) return {id: "result-doc", registry_id: "source-registry", values: {title: "Текущий другой заголовок", original: ["new-file"]}, permissions: ["read", "download"], files: [{id: "old-file", name: "old.docx", version: 1, field_key: "original"}, {id: "new-file", name: "new.docx", version: 2, field_key: "original"}]};
  }});
  await screen.findByRole("button", {name: "old.docx · версия 1"});
  await screen.findByText("Отдел исходного реестра");
  expect(screen.getByText("Исторический заголовок")).toBeTruthy();
  expect(screen.queryByText("Текущий другой заголовок")).toBeNull();
  expect(screen.queryByText("Файлы не добавлены")).toBeNull();
  expect(screen.queryByRole("button", {name: /new.docx/})).toBeNull();
  expect(fetch.mock.calls.some(([url]) => url.includes("/references/instance-departments/items"))).toBe(false);
});

it("does not fetch a restricted result document or render its file IDs and filenames", async () => {
  const result = {id: "closed-result", values: {original: ["hidden-file"]}, comment: "Результат", attachments: [], created_at: "2026-01-01T00:00:00Z", result_document: {restricted: true, label: "Документ результата ограничен", document_id: "closed-document", title: "Закрытый оригинал"}};
  const instance: ProcessInstance = {...initial, state: "completed", process_snapshot: {...initial.process_snapshot, steps: [{...step, form_schema: {sections: [{key: "result", title: "Результат", fields: [{key: "original", label: "Оригинал результата", type: "file"}]}]}}]}, steps: [{...initial.steps[0], state: "completed", result}]};
  const fetch = setup(<ExecutionDetail id="instance" readOnly/>, {instance, viewer: true});
  await screen.findByText("Документ результата ограничен");
  expect(screen.getByText(/Оригиналы — в документах подтверждённого результата/)).toBeTruthy();
  for (const text of ["closed-document", "hidden-file", "Закрытый оригинал", "Файлы не добавлены"]) expect(document.body.textContent).not.toContain(text);
  expect(fetch.mock.calls.some(([url]) => url.includes("/documents/closed-document"))).toBe(false);
});
