import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { ContentMatches } from "../components/ContentMatches";
import { DocumentContent } from "./DocumentContent";
import { Documents } from "./Documents";
import type { Card } from "../engine/schema";

afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers();});
const extraction = {
  id: "extraction", file_id: "file", version_id: "version", version: 2, name: "Постановление.docx",
  sha256: "a".repeat(64), parser: "docx", parser_version: "1", state: "completed", attempts: 1,
  error_code: null, warnings: [], started_at: null, completed_at: null,
  blocks: [{id: "block", kind: "paragraph", ordinal: 0, parent_id: null, page: null, anchor: "paragraph-1", text: "Нормативный порядок подачи заявления"}], block_count: 1,
};
const card = {
  id: "document", registry_id: "registry", document_type_id: "type", schema_version_id: "schema", revision: 1, current_version: 2,
  title: "Постановление", values: {}, schema: {sections: []}, permissions: ["read", "upload", "download"],
  files: [{id: "file", version: 2, name: "Постановление.docx", size: 100, created_at: "2026-10-15T00:00:00Z", mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}],
} as Card;

function setup({reader = false, state = "completed", search = false, blockPage = false, initialMissing = false} = {}) {
  let firstContent = true;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "user", display_name: "User", roles: [reader ? "viewer" : "editor"], csrf_token: "csrf"};
    else if (url.endsWith("/registries")) data = [{id: "registry", name: "Реестр", code: "registry", permissions: ["read"], document_type_ids: ["type"]}];
    else if (url.endsWith("/document-types")) data = [{id: "type", code: "type", name: "Тип"}];
    else if (url.includes("/content/search?")) data = {items: [{document: card, matches: [{source: "file", file_id: "file", version: 2, block_id: "target", block_ordinal: 200, snippet: "Порядок подачи заявления", rank: 0.9, label: "Постановление.docx"}]}], total: 1, page: 1, page_size: 20};
    else if (url.includes("/content/search/export?")) return new Response("Current authorized search results");
    else if (url.includes("/documents?")) data = {items: [], total: 0, page: 1, page_size: 20};
    else if (url.includes("/documents/document/content")) {data = {items: firstContent && initialMissing ? [] : [{...extraction, state, error_code: state === "failed" ? "parse_failed" : null, ...(blockPage ? {block_count: 201, truncated: true} : {})}]}; firstContent = false;}
    else if (url.includes("/content/extractions/extraction/blocks?")) data = {items: [{...extraction.blocks[0], id: "target", ordinal: 200, text: "Целевой блок страницы 2"}], total: 201, page: 2, page_size: 200};
    else if (url.includes("/documents/document?")) data = card;
    else if (url.endsWith("/files/file/extract") && init?.method === "POST") return new Response(JSON.stringify({id: "extraction", state: "pending"}), {status: 202});
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  function Ready() {return useAuth().user ? search ? <Documents readOnly={reader}/> : <DocumentContent card={card} readOnly={reader}/> : null;}
  render(<AuthProvider><Ready/></AuthProvider>);
  return fetch;
}

it("renders source versions and structured blocks without allowing reader extraction writes", async () => {
  setup({reader: true});
  await screen.findByText("Нормативный порядок подачи заявления");
  expect(screen.getByText("Постановление.docx · версия 2 · Содержание извлечено")).toBeTruthy();
  expect(screen.getByRole("button", {name: "Скачать оригинал версии 2"})).toBeTruthy();
  expect(screen.queryByRole("button", {name: "Повторить извлечение"})).toBeNull();
  expect(screen.getByLabelText("Ссылка на блок 1").getAttribute("href")).toBe("/view?document=document&tab=content&file=file&block=block&ordinal=0#block-block");
});

it("keeps a failed source readable and lets an authorized editor request bounded retry", async () => {
  const fetch = setup({state: "failed"});
  expect((await screen.findByRole("alert")).textContent).toMatch(/структуру файла/);
  fireEvent.click(screen.getByRole("button", {name: "Повторить извлечение"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/files/file/extract") && init?.method === "POST")).toBe(true));
  expect(fetch.mock.calls.some(([url]) => url.includes("/documents/document/content?mode=editor"))).toBe(true);
});

it("makes missing digital text explicit without offering unsupported OCR actions", async () => {
  setup({reader: true, state: "no_text"});
  await screen.findByText("Нет цифрового текстового слоя. Распознавание сканов пока не подключено.");
  expect(screen.queryByRole("button", {name: /извлечение/})).toBeNull();
});

it("uses real search API shape and opens a precise source block beyond the first page", async () => {
  const fetch = setup({reader: true, search: true, blockPage: true});
  vi.stubGlobal("URL", class extends URL {static createObjectURL() {return "blob:download";} static revokeObjectURL() {}});
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  fireEvent.click(await screen.findByRole("button", {name: "Реестр"}));
  fireEvent.change(screen.getByLabelText("Поиск по содержанию"), {target: {value: "Порядок подачи"}});
  expect((screen.getByLabelText("Сортировка") as HTMLSelectElement).disabled).toBe(true);
  expect(screen.getByText("Результаты поиска по содержанию упорядочены по релевантности.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Найти"}));
  await screen.findByText("Постановление.docx · версия 2");
  expect(screen.getByText("Порядок").tagName).toBe("MARK");
  fireEvent.click(screen.getByRole("button", {name: "Экспорт CSV"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("/content/search/export?") && new URL(url, "https://example.test").searchParams.get("text") === "Порядок подачи")).toBe(true));
  fireEvent.click(screen.getByRole("button", {name: "Открыть фрагмент источника"}));
  await screen.findByText("Целевой блок страницы 2");
  expect(fetch.mock.calls.some(([url]) => url.includes("/content/extractions/extraction/blocks?page=2&page_size=200&mode=viewer"))).toBe(true);
  expect(fetch.mock.calls.some(([url]) => url.includes("/content/search?") && new URL(url, "https://example.test").searchParams.get("text") === "Порядок подачи")).toBe(true);
});

it("treats search snippets and highlights as literal text, never HTML", () => {
  const {container} = render(<ContentMatches matches={[{source: "metadata", snippet: '<img src=x onerror="alert(1)"> Порядок', label: "Название", rank: 1}]} query="Порядок" onOpen={vi.fn()}/>);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("mark")?.textContent).toBe("Порядок");
  expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
});

it("refreshes asynchronous upload backlog when extraction records have not appeared yet", async () => {
  vi.useFakeTimers();
  const fetch = setup({reader: true, initialMissing: true});
  await act(async () => {});
  expect(screen.getByText("Содержание ещё не обработано")).toBeTruthy();
  await act(async () => {await vi.advanceTimersByTimeAsync(4000);});
  expect(screen.getByText("Нормативный порядок подачи заявления")).toBeTruthy();
  expect(fetch.mock.calls.filter(([url]) => url.includes("/documents/document/content"))).toHaveLength(2);
});
