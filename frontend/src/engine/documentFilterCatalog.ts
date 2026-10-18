import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import { canRead, type DocumentType, type Field, type SchemaVersion } from "./schema";

/** Catalog comes from authorized published schemas, never from the current result page. */
export function filterCatalog(versions: SchemaVersion[], types: DocumentType[], roles: string[], readOnly: boolean): Field[] {
  const candidates = versions.filter(version => version.state === "published").flatMap(version =>
    version.schema.sections.flatMap(section => section.fields).filter(field =>
      field.filterable && !field.visible_when && !["file", "group"].includes(field.type) &&
      canRead(field, "view", roles, {}, readOnly ? "viewer" : "editor")
    ).map(field => ({version, field})));
  const signature = (field: Field) => JSON.stringify([field.type, field.label, field.reference_set_id || null, field.reference_code || null, field.system || null]);
  const definitions = new Map<string, Set<string>>();
  for (const {field} of candidates) {
    if (!definitions.has(field.key)) definitions.set(field.key, new Set());
    definitions.get(field.key)!.add(signature(field));
  }
  const seen = new Set<string>();
  return candidates.flatMap(({version, field}) => {
    const scoped = definitions.get(field.key)!.size > 1;
    const key = scoped ? `schema:${version.id}:${field.key}` : field.key;
    if (seen.has(key)) return [];
    seen.add(key);
    return [{...field, key, ...(scoped ? {label: `${field.label} · ${types.find(type => type.id === version.document_type_id)?.name || "Тип документа"} · версия ${version.version}`} : {})}];
  });
}

export function useDocumentFilterCatalog(typeIds: string[], types: DocumentType[], owner: string, roles: string[], readOnly: boolean) {
  const key = JSON.stringify([owner, readOnly, [...roles].sort(), [...new Set(typeIds)].sort()]);
  const [state, setState] = useState<{key: string; versions: SchemaVersion[]; loading: boolean; error: string}>({key: "", versions: [], loading: false, error: ""});
  useEffect(() => {
    const [, viewer, , ids] = JSON.parse(key) as [string, boolean, string[], string[]];
    const controller = new AbortController();
    setState({key, versions: [], loading: !!ids.length, error: ""});
    void Promise.all(ids.map(async id => {
      try {
        return {versions: await apiRequest<SchemaVersion[]>(`/document-types/${id}/versions?mode=${viewer ? "viewer" : "editor"}`, {signal: controller.signal}), error: ""};
      } catch (failure) {return {versions: [], error: failure instanceof Error ? failure.message : "Не удалось загрузить поля фильтра"};}
    })).then(results => {
      if (!controller.signal.aborted) setState({key, versions: results.flatMap(result => result.versions), loading: false, error: results.find(result => result.error)?.error || ""});
    });
    return () => controller.abort();
  }, [key]);
  return state.key === key ? {...state, fields: filterCatalog(state.versions, types, roles, readOnly)} : {fields: [], loading: !!typeIds.length, error: ""};
}
