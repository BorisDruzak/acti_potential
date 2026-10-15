import type { ProcessSource } from "./processes";
export type KnowledgePermissions = {edit: boolean; publish: boolean; curate: boolean; access?: boolean};
export type KnowledgeTargets = {owner_id: string | null; visibility: "registry" | "private"; document_type_id: string | null; form_schema_id: string | null; process_version_id: string | null; step_key: string | null; characteristics: Record<string, (string | number | boolean)[]>; process_restricted?: boolean; characteristics_restricted?: boolean};
export type KnowledgeOriginal = {document_id?: string; version_id?: string; file_id?: string; name?: string; version?: number; restricted?: boolean; label?: string};
export type TemplateVersion = KnowledgeTargets & {id: string; template_id: string; version: number; revision: number; state: string; name: string; purpose: string; category: string; original: KnowledgeOriginal; permissions: KnowledgePermissions; published_at?: string};
export type Template = Omit<TemplateVersion, "version" | "template_id" | "original"> & {registry_id: string; lifecycle: string; version: TemplateVersion | null; reasons?: string[]};
export type PracticeCase = KnowledgeTargets & {id: string; registry_id: string; revision: number; state: string; lifecycle?: string; name: string; description: string; result: string; category: string; links: ProcessSource[]; confirmed: boolean; recommended: boolean; author_name?: string; created_at?: string; published_at?: string; permissions: KnowledgePermissions; reasons?: string[]};
export type KnowledgePage<T> = {items: T[]; total: number; page: number; page_size: number};
export const emptyTargets: KnowledgeTargets = {owner_id: null, visibility: "registry", document_type_id: null, form_schema_id: null, process_version_id: null, step_key: null, characteristics: {}};
export function writableTargets(value: KnowledgeTargets) {
  if (value.process_restricted || value.characteristics_restricted) throw new Error("Есть недоступные привязки или характеристики. Восстановите доступ либо явно замените недоступные сведения перед сохранением.");
  return {owner_id: value.owner_id, visibility: value.visibility, document_type_id: value.document_type_id, form_schema_id: value.form_schema_id, process_version_id: value.process_version_id, step_key: value.step_key, characteristics: value.characteristics};
}
export function templatePayload(value: TemplateVersion) {
  if (value.original.restricted) throw new Error("Оригинал ограничен. Выберите разрешённый оригинал перед сохранением.");
  return {...writableTargets(value), revision: value.revision, name: value.name, purpose: value.purpose, category: value.category, original: {document_id: value.original.document_id, version_id: value.original.version_id, file_id: value.original.file_id}};
}
export function casePayload(value: PracticeCase) {
  if (value.links.some(link => link.restricted)) throw new Error("Есть недоступные основания. Восстановите доступ либо явно удалите ссылку перед сохранением.");
  return {...writableTargets(value), revision: value.revision, name: value.name, description: value.description, result: value.result, category: value.category, links: value.links.map(link => ({document_id: link.document_id, version_id: link.version_id || null, block_id: link.block_id || null, note: link.note || ""}))};
}
