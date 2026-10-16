import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import { canRead, type Card, type Field, type FormSchema, type SchemaVersion } from "./schema";

export const documentColumns = [
  {key: "title", label: "Наименование документа", sort: "title"},
  {key: "reg_number", label: "Регистрационный номер", sort: "reg_number"},
  {key: "reg_date", label: "Дата регистрации", sort: "reg_date"},
  {key: "document_type", label: "Тип документа"},
  {key: "kind", label: "Вид документа"},
  {key: "topic", label: "Тема"},
  {key: "responsible", label: "Ответственный исполнитель"},
] as const;
export type DocumentColumn = typeof documentColumns[number]["key"];
const normalized = (text: string) => text.trim().toLocaleLowerCase("ru-RU").replaceAll("ё", "е").replace(/\s+/g, " ");

// A list-column heading is an explicit type-level alias for a field. Otherwise
// use its configured label. Never infer ACTI v2 keys or use another schema version.
export function presentationField(schema: FormSchema | undefined, label: string): Field | undefined {
  if (!schema) return;
  const fields = schema.sections.flatMap(section => section.fields);
  const aliases = schema.list_view?.columns.filter(column => normalized(column.label) === normalized(label)) || [];
  if (aliases.length) return aliases.length === 1 ? fields.find(field => field.key === aliases[0].key) : undefined;
  const matches = fields.filter(field => normalized(field.label) === normalized(label));
  return matches.length === 1 ? matches[0] : undefined;
}
export function listValue(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (Array.isArray(value)) return value.map(listValue).join(", ") || "—";
  return typeof value === "object" ? "—" : String(value);
}
export function configuredValue(item: Pick<Card, "values" | "display_values">, schema: FormSchema | undefined, label: string, roles: string[], readOnly: boolean): string {
  const field = presentationField(schema, label);
  if (!field || !Object.hasOwn(item.values || {}, field.key) ||
      !canRead(field, "view", roles, item.values, readOnly ? "viewer" : "editor", schema!.sections.flatMap(section => section.fields))) return "—";
  if (Object.hasOwn(item.display_values || {}, field.key)) return listValue(item.display_values![field.key]);
  if (["reference", "multi_reference", "user", "department", "document", "file", "group"].includes(field.type)) return "—";
  return listValue(item.values[field.key]);
}
export function configuredSort(schema: FormSchema | undefined, label: string): string | undefined {
  const field = presentationField(schema, label);
  if (field?.filterable && !field.visible_when && ["text", "textarea", "integer", "decimal", "date", "datetime", "boolean"].includes(field.type))
    return field.system || `custom:${field.key}`;
}

export function configuredColumnSort(schemas: FormSchema[], label: string): string | undefined {
  const keys = [...new Set(schemas.map(schema => configuredSort(schema, label)))];
  return keys.length === 1 ? keys[0] : undefined;
}

export function useDocumentSchemas(items: Card[], owner: string, readOnly: boolean, enabled: boolean) {
  const mode = readOnly ? "viewer" : "editor";
  const key = `${owner}:${mode}`;
  const requirements = [...new Set(items.filter(item => item.document_type_id && item.schema_version_id)
    .map(item => `${item.document_type_id}:${item.schema_version_id}`))].sort().join("|");
  const [cache, setCache] = useState<{owner: string; types: Record<string, SchemaVersion[]>}>({owner: key, types: {}});
  useEffect(() => {
    if (!enabled || !requirements) return;
    const previous = cache.owner === key ? cache.types : {};
    const needed = [...new Set(requirements.split("|").filter(pair => {
      const [type, version] = pair.split(":");
      return !previous[type]?.some(item => item.id === version);
    }).map(pair => pair.split(":")[0]))];
    if (!needed.length) return;
    const controller = new AbortController();
    void Promise.all(needed.map(async id => {
      try {
        const versions = await apiRequest<SchemaVersion[]>(`/document-types/${id}/versions?mode=${mode}`, {signal: controller.signal});
        return [id, versions.filter(version => version.state === "published")] as const;
      } catch { return [id, []] as const; }
    })).then(results => {
      if (!controller.signal.aborted) setCache(current => ({owner: key, types: {...(current.owner === key ? current.types : {}), ...Object.fromEntries(results)}}));
    });
    return () => controller.abort();
    // Cache completion doesn't change the requested immutable schema identities.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requirements, key, mode, enabled]);
  const types = cache.owner === key ? cache.types : {};
  return {
    byVersion: new Map(Object.values(types).flat().map(version => [version.id, version.schema])),
    forType: (id: string) => (types[id] || []).map(version => version.schema),
  };
}
