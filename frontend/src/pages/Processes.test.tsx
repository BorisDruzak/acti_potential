import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { newProcessStep, processPayload, type ProcessRecord, type ProcessVersion } from "../engine/processes";
import type { Condition, FormSchema } from "../engine/schema";
import { ProcessConditionEditor } from "./ProcessConditions";
import { ProcessBuilder } from "./ProcessBuilder";
import { ProcessView } from "./ProcessView";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
const process: ProcessRecord = {id: "process", registry_id: "registry", code: "example", name: "Подготовка", description: "", owner_id: null, department: "Отдел", category: "Документы", purpose: "Подготовить документ", status: "active"};
const version: ProcessVersion = {id: "version", process_id: "process", version: 1, state: "draft", effective_from: "2026-01-01", revision: 1, steps: [], transitions: []};
const schema: FormSchema = {sections: [{key: "main", title: "Маршрут", fields: [{key: "route", label: "Маршрут подготовки", type: "text", constraints: {choices: ["standard", "urgent", "other"]}}, {key: "amount", label: "Сумма", type: "decimal"}]}]};

function setup({viewer = false, current = version} = {}) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown = [];
    if (url.endsWith("/users/me")) data = {id: "user", display_name: "User", roles: [viewer ? "viewer" : "admin"], csrf_token: "csrf"};
    else if (url.includes("/form-schemas/")) data = {id: "schema", document_type_id: "type", version: 1, state: "published", schema};
    else if (url.includes("/basis?")) data = {items: [{restricted: true, label: "Основание ограничено", document_id: "DO-NOT-RENDER-ID", document: {title: "Секретный акт"}}]};
    else if (url.includes("/process-versions/version") && init?.method === "PUT") data = {...current, ...JSON.parse(String(init.body)), revision: 2};
    else if (url.includes("/validate")) data = {valid: true};
    else if (url.includes("/preview?")) data = {next_step_keys: ["finish"], transition_key: "edge"};
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  function Ready() {return useAuth().user ? viewer ? <ProcessView process={process} version={current} readOnly/> : <ProcessBuilder process={process} version={current} types={[]} onSaved={() => {}}/> : null;}
  render(<AuthProvider><Ready/></AuthProvider>);
  return fetch;
}

it("starts a five-step branching example as an editable draft without publishing or executing it", async () => {
  const fetch = setup();
  fireEvent.click(await screen.findByRole("button", {name: "Синтетический пример подготовки документа / закупки"}));
  const picker = screen.getByLabelText("Перейти к шагу") as HTMLSelectElement;
  expect(picker.options.length).toBe(6);
  const keys = Array.from(picker.options).map(option => option.value).filter(Boolean);
  expect(new Set(keys).size).toBe(5);
  expect(screen.getByText(/Создан синтетический черновик/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Сохранить черновик процесса"}));
  await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/process-versions/version") && init?.method === "PUT")).toBe(true));
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/process-versions/version") && init?.method === "PUT")!;
  const body = JSON.parse(String(request[1]?.body));
  expect(body.steps).toHaveLength(5);
  expect(body.transitions.filter((edge: {is_else: boolean}) => edge.is_else)).toHaveLength(1);
  expect(body.steps.map((step: {key: string}) => step.key)).toEqual(keys);
  expect(document.body.textContent).not.toContain("Ключ шага:");
  expect(fetch.mock.calls.some(([url]) => url.includes("/publish") || url.includes("/instances"))).toBe(false);
});

it("serializes writable graph fields only and refuses to overwrite restricted references", () => {
  const start = {...newProcessStep(), form_schema: schema, sources: [{document_id: "doc", version_id: "doc-v1", block_id: "block", restricted: false, note: "Источник"}]};
  const projected = {...version, steps: [start], transitions: [{key: "edge", from_step: start.key, to_step: "finish", label: "Далее", condition: null, is_else: false, restricted: false, ordinal: 3}]} as ProcessVersion;
  const payload = processPayload(projected);
  expect(payload.steps[0]).not.toHaveProperty("form_schema");
  expect(payload.steps[0].sources[0]).not.toHaveProperty("restricted");
  expect(payload.transitions[0]).not.toHaveProperty("ordinal");
  expect(() => processPayload({...projected, steps: [{...start, sources: [{restricted: true, label: "Основание ограничено"}]}]})).toThrow(/недоступные основания/);
});

it("uses declared choices for typed conditions and removes operands for presence operators", () => {
  const changed = vi.fn();
  function Editor() {const [condition, setCondition] = useState<Condition>({field: "route", op: "eq", value: "standard"}); return <ProcessConditionEditor schema={schema} condition={condition} onChange={next => {setCondition(next); changed(next);}} references={[]} registryId="registry"/>;}
  render(<Editor/>);
  const operand = screen.getByLabelText("Значение условия") as HTMLSelectElement;
  expect(operand.tagName).toBe("SELECT");
  fireEvent.change(operand, {target: {value: "1"}});
  expect(changed).toHaveBeenLastCalledWith({field: "route", op: "eq", value: "urgent"});
  fireEvent.change(screen.getByLabelText("Оператор условия"), {target: {value: "absent"}});
  expect(changed).toHaveBeenLastCalledWith({field: "route", op: "absent"});
  expect(screen.queryByLabelText("Значение условия")).toBeNull();
});

it("keeps restricted process bases generic and exposes no hidden identifiers or titles", async () => {
  const current = {...version, state: "published", steps: [{...newProcessStep(), key: "start", title: "Первый шаг", is_start: true, is_terminal: true, sources: [{restricted: true}]}]};
  setup({viewer: true, current});
  fireEvent.click(await screen.findByRole("button", {name: "Почему этот шаг существует?"}));
  await screen.findByText("Основание ограничено");
  expect(document.body.textContent).not.toContain("DO-NOT-RENDER-ID");
  expect(document.body.textContent).not.toContain("Секретный акт");
  expect(screen.queryByRole("button", {name: /Скачать оригинал/})).toBeNull();
  expect(screen.queryByRole("button", {name: "Опубликовать процесс"})).toBeNull();
});

it("checks a published viewer preview with local answers and no configuration or execution writes", async () => {
  const current = {...version, state: "published", steps: [{...newProcessStep(), key: "start", title: "Первый шаг", is_start: true, form_schema_id: "schema"}, {...newProcessStep(), key: "finish", title: "Готово", is_terminal: true}], transitions: [{key: "edge", from_step: "start", to_step: "finish", label: "Далее", condition: null, is_else: false}]};
  const fetch = setup({current});
  fireEvent.click(await screen.findByRole("button", {name: "Как читатель"}));
  fireEvent.change(await screen.findByLabelText("Маршрут подготовки"), {target: {value: "urgent"}});
  fireEvent.click(screen.getByRole("button", {name: "Проверить маршрут"}));
  await screen.findByText("Следующий шаг: Готово");
  const request = fetch.mock.calls.find(([url, init]) => url.endsWith("/process-versions/version/preview?mode=viewer") && init?.method === "POST");
  expect(JSON.parse(String(request?.[1]?.body))).toEqual({step_key: "start", values: {route: "urgent"}});
  fireEvent.change(screen.getByLabelText("Шаг процесса"), {target: {value: "finish"}});
  expect(screen.queryByText("Следующий шаг: Готово")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "Проверить маршрут"}));
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.endsWith("/preview?mode=viewer"))).toHaveLength(2));
  const nextRequest = fetch.mock.calls.filter(([url]) => url.endsWith("/preview?mode=viewer"))[1];
  expect(JSON.parse(String(nextRequest[1]?.body))).toEqual({step_key: "finish", values: {}});
  expect(fetch.mock.calls.some(([url, init]) => init?.method === "PUT" || url.includes("/instances"))).toBe(false);
});
