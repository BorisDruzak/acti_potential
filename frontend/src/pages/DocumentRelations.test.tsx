import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { DocumentRelations, RelationStatusHistory } from "./DocumentRelations";
import { RelationEditor } from "./RelationEditor";
import type { Card } from "../engine/schema";
import type { DocumentRelation } from "../engine/relations";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
const card = {id: "source", title: "Изменяющий акт", revision: 1, values: {}, schema: {sections: []}, files: [], permissions: ["read", "update", "download"]} as unknown as Card;
const target = {...card, id: "target", title: "Исходный акт"};
const relation: DocumentRelation = {
  id: "relation", source_document: card, target_document: target, source_version_id: "source-v1", target_version_id: "target-v1",
  source_version: 1, target_version: 1, source_block_id: "source-block", target_block_id: "target-block",
  source_anchor: {file_id: "source-file", block_id: "source-block", ordinal: 0, version_id: "source-v1"},
  target_anchor: {file_id: "target-file", block_id: "target-block", ordinal: 0, version_id: "target-v1"},
  source_snippet: "Точный пункт изменения", relation_type: "changes", source: "manual", state: "proposed", description: "Изменение пункта", external_target: null,
  created_at: "2026-10-15T10:00:00Z", decided_at: null, effective_date: null, reason: "", created_by_name: "Редактор",
  permissions: {confirm: false, reject: false},
};

function setup({reader = false, canConfirm = false, editor = false, history = false, basis = false} = {}) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "user", display_name: "User", roles: [reader ? "viewer" : "editor"], csrf_token: "csrf"};
    else if (url.includes("/relation-options?")) {const id = url.includes("/documents/source/") ? "source" : "target"; data = {versions: [{id: id + "-v1", version: 1, files: [{id: id + "-file", name: id + ".docx", extractions: [{id: id + "-extraction", state: "completed"}]}]}], permissions: {propose: true, confirm: canConfirm}};}
    else if (url.includes("/content/extractions/")) {const id = url.includes("source-extraction") ? "source" : "target"; data = {items: [{id: id + "-block", ordinal: 0, text: "Точный пункт", page: null}], total: 1};}
    else if (url.includes("/relation-graph?")) data = {nodes: [{id: "source", title: card.title}, {id: "target", title: target.title}], edges: [{id: "relation", source_document_id: "source", target_document_id: "target", relation_type: "changes", label: "Изменяет", inverse_label: "Изменён документом", state: "proposed"}], truncated: false};
    else if (url.includes("/relations?")) data = {items: [{...relation, permissions: {confirm: canConfirm, reject: canConfirm}}], total: 1, page: 1, page_size: 20, status_effects: []};
    else if (url.includes("/relation-history?")) data = {items: [{relation_id: "relation", action: "confirmed", reason: "Проверено", created_at: "2026-10-15T10:00:00Z", effective_date: "2030-01-01", applies_now: false, actor_name: "Уполномоченный"}], total: 45};
    else if (url.includes("/document-relations/relation/basis?")) data = {document: card, anchor: {...relation.source_anchor, version: 1}, files: [{id: "source-file", name: "source.docx", version: 1, version_id: "source-v1"}], blocks: [{id: "source-block", text: "Точный пункт закреплённой версии", ordinal: 0}]};
    else if (url.includes("/documents?")) data = {items: [target], total: 1};
    else if (url.endsWith("/document-relations") && init?.method === "POST") return new Response(JSON.stringify(relation), {status: 201});
    else if (url.includes("/document-relations/relation/")) data = {...relation, state: "confirmed"};
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  const saved = vi.fn(); const navigate = vi.fn();
  function Ready() {return useAuth().user ? editor ? <RelationEditor card={card} mode="editor" onSaved={saved} onClose={() => {}}/> : history ? <RelationStatusHistory card={card} readOnly={reader} onNavigate={navigate}/> : <DocumentRelations card={card} readOnly={reader} basis={basis} onNavigate={navigate}/> : null;}
  render(<AuthProvider><Ready/></AuthProvider>);
  return {fetch, saved, navigate};
}

it("keeps proposals distinct and hides decisions without explicit relation permissions", async () => {
  setup({basis: true});
  await screen.findByText("Предложение не меняет официальный статус документа.");
  expect(screen.queryByRole("button", {name: "Подтвердить связь"})).toBeNull();
  expect(screen.getByText("Подтверждённых оснований изменения статуса нет.")).toBeTruthy();
  expect(screen.getByText("Предложение не меняет официальный статус документа.")).toBeTruthy();
  expect(screen.getByText(/автор: Редактор/)).toBeTruthy();
});

it("reader has graph navigation and a complete list without proposal or decision controls", async () => {
  const {navigate, fetch} = setup({reader: true, canConfirm: true});
  await screen.findByText("Предложение не меняет официальный статус документа.");
  expect(screen.queryByRole("button", {name: "Предложить связь документа"})).toBeNull();
  expect(screen.queryByRole("button", {name: "Подтвердить связь"})).toBeNull();
  const graph = await screen.findByRole("region", {name: "Интерактивная схема связей"});
  fireEvent.click(within(graph).getByRole("button", {name: /Исходный акт/}));
  expect(navigate).toHaveBeenCalledWith(expect.objectContaining({documentId: "target"}));
  fireEvent.change(screen.getByLabelText("Фильтр типа связи"), {target: {value: "repeals"}});
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("type=repeals"))).toBe(true));
});

it("proposes a relation with exact source and target version and paragraph IDs from authorized pickers", async () => {
  const {fetch, saved} = setup({editor: true});
  await screen.findByRole("option", {name: "Версия 1"});
  fireEvent.change(screen.getByLabelText("Версия: Исходный документ"), {target: {value: "source-v1"}});
  fireEvent.change(screen.getByLabelText("Оригинал: Исходный документ"), {target: {value: "source-extraction"}});
  await screen.findByRole("option", {name: "1. Точный пункт"});
  fireEvent.change(screen.getByLabelText("Фрагмент: Исходный документ"), {target: {value: "source-block"}});
  fireEvent.change(screen.getByLabelText("Связанный документ", {exact: true}), {target: {value: "target"}});
  const targetPicker = await screen.findByRole("group", {name: "Связанный документ: версия и основание"});
  await within(targetPicker).findByRole("option", {name: "Версия 1"});
  fireEvent.change(screen.getByLabelText("Версия: Связанный документ"), {target: {value: "target-v1"}});
  fireEvent.change(screen.getByLabelText("Оригинал: Связанный документ"), {target: {value: "target-extraction"}});
  await within(targetPicker).findByRole("option", {name: "1. Точный пункт"});
  fireEvent.change(screen.getByLabelText("Фрагмент: Связанный документ"), {target: {value: "target-block"}});
  fireEvent.change(screen.getByLabelText("Тип связи"), {target: {value: "changes"}});
  fireEvent.change(screen.getByLabelText("Пояснение связи"), {target: {value: "Пункт изменяет исходное положение"}});
  fireEvent.click(screen.getByRole("button", {name: "Предложить связь"}));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/document-relations") && init?.method === "POST");
  expect(JSON.parse(String(request![1]?.body))).toMatchObject({source_document_id: "source", target_document_id: "target", source_version_id: "source-v1", target_version_id: "target-v1", source_block_id: "source-block", target_block_id: "target-block", relation_type: "changes", source: "manual"});
});

it("requires an explicit dated reason before confirmation and preserves the concrete basis link", async () => {
  const {fetch} = setup({canConfirm: true});
  fireEvent.click(await screen.findByRole("button", {name: "Подтвердить связь"}));
  fireEvent.change(screen.getByLabelText("Основание решения"), {target: {value: "Основание сверено с оригиналом"}});
  fireEvent.change(screen.getByLabelText("Дата действия решения"), {target: {value: "2026-10-15"}});
  fireEvent.click(screen.getByRole("button", {name: "Сохранить решение о связи"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/document-relations/relation/confirm") && JSON.parse(String(init?.body)).reason === "Основание сверено с оригиналом")).toBe(true));
});

it("history distinguishes future or old-version decisions and opens their pinned original basis", async () => {
  const {fetch} = setup({reader: true, history: true});
  await screen.findByText("Это решение не создаёт действующего изменения статуса текущей версии.");
  const details = screen.getByText("Основание исходного документа").closest("details")!;
  details.open = true; fireEvent(details, new Event("toggle"));
  await screen.findByText("Точный пункт закреплённой версии");
  expect(screen.getByRole("button", {name: "Скачать основание версии 1"})).toBeTruthy();
  expect(screen.getByText(/Реквизиты карточки показаны в текущем/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Вперёд"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("relation-history?mode=viewer&page=2&page_size=20"))).toBe(true));
});

it("records a user-verified external HTTPS basis without fetching the external site", async () => {
  const {fetch, saved} = setup({editor: true});
  fireEvent.click(await screen.findByLabelText("Внешний акт"));
  fireEvent.change(screen.getByLabelText("Название внешнего акта"), {target: {value: "Внешнее постановление"}});
  fireEvent.change(screen.getByLabelText("HTTPS-ссылка на акт"), {target: {value: "https://example.org/act"}});
  fireEvent.change(screen.getByLabelText("Реквизиты внешнего акта"), {target: {value: "№ 7"}});
  fireEvent.change(screen.getByLabelText("Версия внешнего акта"), {target: {value: "2026-01"}});
  fireEvent.change(screen.getByLabelText("Дата действия внешнего акта"), {target: {value: "2026-01-01"}});
  fireEvent.click(screen.getByLabelText("Я проверил ссылку, реквизиты и версию источника"));
  fireEvent.change(screen.getByLabelText("Пояснение связи"), {target: {value: "Источник проверен автором"}});
  fireEvent.click(screen.getByRole("button", {name: "Предложить связь"}));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/document-relations") && init?.method === "POST");
  expect(JSON.parse(String(request![1]?.body)).external_target).toMatchObject({url: "https://example.org/act", verified: true, version: "2026-01", effective_date: "2026-01-01"});
  expect(fetch.mock.calls.every(([url]) => !url.startsWith("https://example.org"))).toBe(true);
});
