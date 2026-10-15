import type { ContentTarget } from "./content";
import type { Card } from "./schema";

export const relationTypes = [
  ["changes", "Изменяет", "Изменён на основании"],
  ["repeals", "Отменяет", "Утратил силу на основании"],
  ["replaces", "Заменяет", "Заменён на основании"],
  ["approves", "Утверждает", "Утверждён на основании"],
  ["attachment", "Является приложением к", "Имеет приложение"],
  ["reference", "Ссылается на", "На него ссылается"],
  ["in_execution", "Принят во исполнение", "Исполняется документом"],
  ["previous_revision", "Имеет предыдущую редакцию", "Имеет следующую редакцию"],
  ["topic", "Связан по предмету", "Связан по предмету"],
] as const;
export const relationStates: Record<string, string> = {proposed: "Предложена", confirmed: "Подтверждена", rejected: "Отклонена"};
export type RelationAnchor = {file_id?: string; block_id?: string; ordinal?: number; block_ordinal?: number; version_id?: string};
export type DocumentRelation = {
  id: string; source_document: Card; target_document: Card | null;
  source_version_id: string | null; target_version_id: string | null;
  source_version?: number | null; target_version?: number | null;
  source_block_id: string | null; target_block_id: string | null;
  source_anchor?: RelationAnchor | null; target_anchor?: RelationAnchor | null;
  source_snippet?: string; target_snippet?: string;
  relation_type: string; source: string; state: string; description: string;
  external_target: {url: string; title: string; identifier: string; version: string; effective_date: string; verified: boolean} | null;
  created_at: string; decided_at: string | null; reason: string; effective_date: string | null;
  created_by?: string; decided_by?: string | null; created_by_name?: string; decided_by_name?: string;
  permissions: {confirm: boolean; reject: boolean};
};
export type RelationVersion = {id: string; version: number; files: {id: string; name: string; extractions: {id: string; state: string}[]}[]};
export type RelationSelection = {version_id: string; block_id: string | null};
export type RelationNavigation = {documentId: string; versionId?: string; contentTarget?: ContentTarget};
export function relationLabel(type: string, inverse = false) {
  const entry = relationTypes.find(item => item[0] === type);
  return entry ? entry[inverse ? 2 : 1] : "Связь документов";
}
