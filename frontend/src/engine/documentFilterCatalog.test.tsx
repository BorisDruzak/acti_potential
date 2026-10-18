import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { filterCatalog, useDocumentFilterCatalog } from "./documentFilterCatalog";
import type { Field, SchemaVersion } from "./schema";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
const version = (id: string, type: string, fields: Field[]): SchemaVersion => ({id, document_type_id: type, version: 1, state: "published", schema: {sections: [{key: "s", title: "S", fields}]}});
it("includes every eligible publication and participant field without requiring search results", async () => {
  const fields: Field[] = [
    {key: "should_publish", label: "Подлежит опубликованию", type: "boolean", filterable: true},
    {key: "published_where", label: "Где опубликован", type: "reference", reference_code: "publishers", filterable: true},
    {key: "published_date", label: "Дата опубликования", type: "date", filterable: true},
    {key: "publication_number", label: "Номер публикации", type: "text", filterable: true},
    {key: "removed_from_control", label: "Снято с контроля", type: "boolean", filterable: true},
    {key: "responsible", label: "Ответственный", type: "user", filterable: true},
  ];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([version("v", "t", fields)]))));
  const {result} = renderHook(() => useDocumentFilterCatalog(["t"], [{id: "t", code: "t", name: "Тип"}], "owner", ["editor"], false));
  await waitFor(() => expect(result.current.fields.map(field => field.key)).toEqual(fields.map(field => field.key)));
});
it("excludes hidden, conditional, unsupported and unpublished fields", () => {
  const fields: Field[] = [
    {key: "yes", label: "Да", type: "boolean", filterable: true},
    {key: "secret", label: "Секрет", type: "text", filterable: true, read_roles: ["admin"]},
    {key: "editor", label: "Редактор", type: "text", filterable: true, modes: ["editor"]},
    {key: "conditional", label: "Условие", type: "text", filterable: true, visible_when: {field: "yes", op: "present"}},
    {key: "file", label: "Файл", type: "file", filterable: true},
  ];
  expect(filterCatalog([version("v", "t", fields), {...version("draft", "t", fields), state: "draft"}], [], ["viewer"], true).map(field => field.key)).toEqual(["yes"]);
});
it("keeps conflicting type, reference and label definitions scoped to their original schema", () => {
  const schemas = [version("v1", "t1", [{key: "same", label: "Место", type: "reference", reference_code: "one", filterable: true}]),
    version("v2", "t2", [{key: "same", label: "Источник", type: "reference", reference_code: "two", filterable: true}])];
  const catalog = filterCatalog(schemas, [{id: "t1", code: "a", name: "Тип А"}, {id: "t2", code: "b", name: "Тип Б"}], ["editor"], false);
  expect(catalog.map(field => field.key)).toEqual(["schema:v1:same", "schema:v2:same"]);
  expect(catalog.map(field => field.reference_code)).toEqual(["one", "two"]);
  expect(catalog[0].label).toContain("Тип А");
  expect(catalog[1].label).toContain("Тип Б");
});
