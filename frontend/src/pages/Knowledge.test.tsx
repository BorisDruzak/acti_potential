import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { casePayload, emptyTargets, templatePayload, type PracticeCase, type TemplateVersion } from "../engine/knowledge";
import { newProcessStep, processPayload } from "../engine/processes";
import { KnowledgeCatalog, RelatedKnowledge } from "./Knowledge";
import { PracticeEditor } from "./KnowledgeEditor";
import { CaseDetail, KnowledgeBasis, TemplateDetail } from "./KnowledgeView";
import * as client from "../api/client";

afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();});
const rights = {edit: true, publish: true, curate: true, access: true};
const template: TemplateVersion = {...emptyTargets, id: "tv1", template_id: "template", revision: 1, version: 1, state: "published", name: "Образец подготовки", purpose: "Подготовить проект", category: "Подготовка", original: {document_id: "doc", version_id: "doc-v1", file_id: "file1", name: "original-v1.docx", version: 1, restricted: false}, permissions: rights};
const practice: PracticeCase = {...emptyTargets, id: "case", registry_id: "registry", revision: 1, state: "published", name: "Прошлый случай", description: "Как подготовили проект", result: "Проект принят", category: "Подготовка", links: [], confirmed: false, recommended: false, permissions: rights};
type ResponseHandler = (url: string, init?: RequestInit) => unknown;
function setup(element: React.ReactNode, handler: ResponseHandler = () => undefined, viewer = false) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data = handler(url, init);
    if (url.endsWith("/users/me")) data = {id: "user", display_name: "User", roles: [viewer ? "viewer" : "editor"], csrf_token: "csrf"};
    else if (data === undefined && url.endsWith("/registries")) data = [{id: "registry", name: "Реестр", document_type_ids: [], permissions: ["read", "create", "update", "curate_knowledge"]}];
    else if (data === undefined && url.includes("/knowledge/related")) data = {templates: [], cases: []};
    else if (data === undefined && url.includes("/knowledge/templates") && url.includes("/versions")) data = [template];
    else if (data === undefined && url.includes("/knowledge/templates/template")) data = {...template, id: "template", registry_id: "registry", lifecycle: "active", version: template};
    else if (data === undefined && url.includes("/knowledge/cases/case") && !url.includes("/history")) data = practice;
    else if (data === undefined && /\/knowledge\/(templates|cases)\?/.test(url)) data = {items: [], total: 0, page: 1, page_size: 20};
    return new Response(JSON.stringify(data === undefined ? [] : data), {status: 200});
  });
  vi.stubGlobal("fetch", fetch);
  function Ready() {return useAuth().user ? element : null;}
  render(<AuthProvider><Ready/></AuthProvider>);
  return fetch;
}

it("whitelists writable knowledge and process template pins without changing historical objects", () => {
  expect(templatePayload(template).original).toEqual({document_id: "doc", version_id: "doc-v1", file_id: "file1"});
  expect(() => templatePayload({...template, original: {restricted: true}})).toThrow(/Оригинал ограничен/);
  expect(() => casePayload({...practice, links: [{restricted: true, label: "Основание ограничено"}]})).toThrow(/недоступные основания/);
  expect(() => casePayload({...practice, process_restricted: true})).toThrow(/недоступные привязки/);
  expect(() => templatePayload({...template, characteristics_restricted: true})).toThrow(/недоступные привязки/);
  const step = {...newProcessStep(), template_versions: [{template_version_id: template.id, name: template.name, version: 1, restricted: false}]};
  const version = {id: "pv", process_id: "p", version: 2, state: "draft", revision: 1, effective_from: "2026-01-01", steps: [step], transitions: []};
  expect(processPayload(version).steps[0].template_versions).toEqual(["tv1"]);
  expect(step.template_versions[0].name).toBe(template.name);
  expect(() => processPayload({...version, steps: [{...step, template_versions: [{restricted: true}]}]})).toThrow(/недоступные основания/);
});

it("downloads the selected historical template version while viewer actions remain read only", async () => {
  const download = vi.spyOn(client, "saveExport").mockResolvedValue();
  setup(<TemplateDetail id="template" readOnly/>, url => url.includes("/templates/template/versions") ? [{...template, id: "tv2", version: 2, original: {...template.original, file_id: "file2", name: "original-v2.docx"}}, template] : undefined, true);
  await screen.findByRole("heading", {name: "Образец подготовки · версия 1"});
  fireEvent.change(screen.getByLabelText("Версия шаблона"), {target: {value: "tv2"}});
  await screen.findByRole("heading", {name: "Образец подготовки · версия 2"});
  fireEvent.change(screen.getByLabelText("Версия шаблона"), {target: {value: "tv1"}});
  fireEvent.click(screen.getByRole("button", {name: "Скачать шаблон версии 1"}));
  await waitFor(() => expect(download).toHaveBeenCalledWith("/knowledge/template-versions/tv1/download?mode=viewer", "original-v1.docx"));
  expect(screen.queryByRole("button", {name: "Новая версия шаблона"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Изменить видимость и состояние"})).toBeNull();
});

it("records an explicit reason for curator confirmation without rewriting published content", async () => {
  const fetch = setup(<CaseDetail id="case" readOnly={false}/>);
  fireEvent.click(await screen.findByRole("button", {name: "Подтвердить случай"}));
  const save = screen.getByRole("button", {name: "Сохранить решение по практике"}) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Причина решения по практике"), {target: {value: "Проверены результат и основания"}});
  fireEvent.click(save);
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/cases/case/curate") && init?.method === "POST")).toBe(true));
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/cases/case/curate") && init?.method === "POST")!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({action: "confirm", reason: "Проверены результат и основания"});
  expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
});

it("keeps restricted single template bases and case placeholders generic despite extra hidden data", async () => {
  setup(<KnowledgeBasis path="/knowledge/template-versions/tv1/basis" readOnly/>, url => url.includes("/basis?") ? {restricted: true, label: "Оригинал ограничен", document: {id: "secret-id", title: "Закрытый оригинал"}, files: [{id: "secret-file", name: "secret.docx"}], blocks: [{id: "secret-block", text: "Секретный текст"}]} : undefined, true);
  fireEvent.click(await screen.findByRole("button", {name: "Проверить основания знания"}));
  await screen.findByText("Основание ограничено");
  for (const text of ["secret-id", "Закрытый оригинал", "secret.docx", "Секретный текст"]) expect(document.body.textContent).not.toContain(text);
  expect(screen.queryByRole("button", {name: /Скачать основание/})).toBeNull();
});

it("creates a private descriptive case as a draft with no implicit publication or AI promotion", async () => {
  const saved = vi.fn();
  const fetch = setup(<PracticeEditor registryId="registry" onSaved={saved} onClose={() => {}}/>, (url, init) => url.endsWith("/knowledge/cases") && init?.method === "POST" ? {id: "new-case"} : undefined);
  fireEvent.change(await screen.findByLabelText("Название случая"), {target: {value: "Синтетический пример"}});
  fireEvent.change(screen.getByLabelText(/^Описание выполненного случая/), {target: {value: "Собрали проверяемые материалы"}});
  fireEvent.change(screen.getByLabelText(/^Результат случая/), {target: {value: "Проект подготовлен"}});
  fireEvent.change(screen.getByLabelText("Видимость знания"), {target: {value: "private"}});
  await screen.findByRole("option", {name: "Реестр", exact: true});
  fireEvent.change(screen.getByLabelText("Реестр знания"), {target: {value: "registry"}});
  fireEvent.click(screen.getByRole("button", {name: "Сохранить случай"}));
  await waitFor(() => expect(saved).toHaveBeenCalledWith("new-case"));
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/knowledge/cases") && init?.method === "POST")!;
  const body = JSON.parse(String(request[1]?.body));
  expect(body).toMatchObject({registry_id: "registry", visibility: "private", description: "Собрали проверяемые материалы", result: "Проект подготовлен"});
  expect(body).not.toHaveProperty("revision");
  expect(fetch.mock.calls.some(([url]) => url.includes("/publish") || url.includes("/curate"))).toBe(false);
});

it("shows authorized contextual rationale and keeps related reader detail free of curation controls", async () => {
  setup(<RelatedKnowledge processVersionId="pv1" stepKey="start" readOnly/>, url => url.includes("/knowledge/related") ? {templates: [], cases: [{...practice, reasons: ["Связано с этим процессом"]}]} : undefined, true);
  fireEvent.click(await screen.findByRole("button", {name: "Как делали раньше"}));
  await screen.findByText("Почему подходит: Связано с этим процессом");
  fireEvent.click(screen.getByRole("button", {name: /Прошлый случай/}));
  await screen.findByRole("heading", {name: "Прошлый случай"});
  expect(screen.queryByRole("button", {name: "Подтвердить случай"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Редактировать случай"})).toBeNull();
});

it("sends viewer metadata/content search and type filters to the paginated catalog API", async () => {
  const fetch = setup(<KnowledgeCatalog kind="templates" readOnly/>, url => url.endsWith("/document-types") ? [{id: "type", name: "Постановление"}] : undefined, true);
  fireEvent.change(await screen.findByLabelText("Поиск знаний"), {target: {value: "синтетический"}});
  await screen.findByRole("option", {name: "Постановление"});
  fireEvent.change(screen.getByLabelText("Фильтр типа знания"), {target: {value: "type"}});
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("mode=viewer") && url.includes("document_type_id=type") && new URL(url, "https://example.test").searchParams.get("text") === "синтетический")).toBe(true));
  expect(screen.queryByRole("button", {name: "Создать шаблон"})).toBeNull();
});
