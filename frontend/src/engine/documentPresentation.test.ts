import { expect, it } from "vitest";
import { configuredColumnSort, configuredSort, configuredValue, presentationField } from "./documentPresentation";
import type { Card, FormSchema } from "./schema";

it("uses configured list aliases with arbitrary field keys and does not guess legacy keys", () => {
  const schema: FormSchema = {sections: [{key: "a", title: "A", fields: [{key: "unrelated", label: "Вопрос", type: "text", filterable: true}]}], list_view: {columns: [{key: "unrelated", label: "Тема"}]}};
  const item = {values: {unrelated: "Доступная тема"}, display_values: {unrelated: "Доступная тема"}};
  expect(configuredValue(item, schema, "Тема", ["editor"], false)).toBe("Доступная тема");
  expect(configuredSort(schema, "Тема")).toBe("custom:unrelated");
  expect(configuredValue(item, undefined, "Тема", ["editor"], false)).toBe("—");
  expect(presentationField(schema, "Вид документа")).toBeUndefined();
});
it("never renders missing, role-hidden or condition-hidden values, or unresolved identity UUIDs", () => {
  const schema: FormSchema = {sections: [{key: "a", title: "A", fields: [
    {key: "kind", label: "Вид документа", type: "text", read_roles: ["admin"]},
    {key: "flag", label: "Условие", type: "boolean"},
    {key: "topic", label: "Тема", type: "text", visible_when: {field: "flag", op: "eq", value: true}, filterable: true},
    {key: "owner", label: "Ответственный исполнитель", type: "user"},
  ]}]};
  const item = {values: {kind: "Секрет", topic: "Закрыто условием", owner: "opaque-id", flag: false}, display_values: {kind: "Секрет", topic: "Закрыто условием"}} as unknown as Card;
  expect(configuredValue(item, schema, "Вид документа", ["editor"], false)).toBe("—");
  expect(configuredValue(item, schema, "Тема", ["editor"], false)).toBe("—");
  expect(configuredValue(item, schema, "Ответственный исполнитель", ["editor"], false)).toBe("—");
  expect(configuredSort(schema, "Тема")).toBeUndefined();
  expect(configuredSort(schema, "Ответственный исполнитель")).toBeUndefined();
});
it("does not map ambiguous labels or expose a raw value behind a redacted display value", () => {
  const schema: FormSchema = {sections: [{key: "a", title: "A", fields: [{key: "a", label: "Тема", type: "text"}, {key: "b", label: "Тема", type: "text"}]}]};
  expect(presentationField(schema, "Тема")).toBeUndefined();
  schema.list_view = {columns: [{key: "a", label: "Тема"}]};
  expect(configuredValue({values: {a: "private"}, display_values: {a: null}} as unknown as Card, schema, "Тема", ["editor"], false)).toBe("—");
});

it("only sorts a configured column when all published versions agree across result pages", () => {
  const version = (key: string): FormSchema => ({sections: [{key: "s", title: "S", fields: [{key, label: "Тема", type: "text", filterable: true}]}]});
  expect(configuredColumnSort([version("topic_old"), version("topic_new")], "Тема")).toBeUndefined();
  expect(configuredColumnSort([version("topic"), version("topic")], "Тема")).toBe("custom:topic");
  expect(configuredColumnSort([version("topic"), {sections: []}], "Тема")).toBeUndefined();
  expect(configuredColumnSort([], "Тема")).toBeUndefined();
});
