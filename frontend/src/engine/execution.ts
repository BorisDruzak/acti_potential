import type { Values } from "./schema";
import type { ProcessSource, ProcessVersion } from "./processes";
export type ExecutionAttachment = ProcessSource;
export type StepResult = {id: string; values: Values; comment: string; attachments: ExecutionAttachment[]; created_at: string; actor_name?: string; result_document?: {document_id?: string; revision?: number; title?: string; restricted?: boolean; label?: string} | null};
export type ChecklistItem = {id: string; label: string; kind: "input" | "output" | "control"; required: boolean; checked: boolean};
export type StepExecution = {id: string; step_key: string; attempt: number; state: string; assignee_id: string | null; assignee_name?: string | null; started_at?: string | null; completed_at?: string | null; deadline_at?: string | null; overdue: boolean; checklist: ChecklistItem[]; result?: StepResult | null};
export type ProcessInstance = {id: string; registry_id: string; process_version_id: string; name: string; state: string; revision: number; created_by: string; created_by_name?: string; assignee_id: string | null; assignee_name?: string | null; current_step_key: string | null; deadline_at: string | null; overdue: boolean; created_at?: string; process_snapshot: Pick<ProcessVersion, "metadata_snapshot" | "steps" | "transitions"> & {version?: number}; steps: StepExecution[]; permissions: {control: boolean; work: boolean}};
export type ExecutionPage<T> = {items: T[]; total: number; page: number; page_size: number};
export type ExecutionEvent = {id: string; action: string; actor_name?: string; created_at: string; step_key?: string | null; reason?: string | null};
export const executionStates: [string, string][] = [["pending", "Ожидается"], ["active", "В работе"], ["suspended", "Приостановлено"], ["completed", "Завершено"], ["cancelled", "Отменено"]];
export function executionState(state: string) {return executionStates.find(([key]) => key === state)?.[1] || (state === "returned" ? "Возвращён на исправление" : "Состояние дела");}
export function localDeadline(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
export function utcDeadline(value: string) {
  if (!value) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) throw new Error("Укажите корректную дату и время срока.");
  return date.toISOString();
}
export function resultPayload(values: Values, comment: string, attachments: ExecutionAttachment[]) {
  if (attachments.some(item => item.restricted)) throw new Error("В результате есть недоступные документы. Восстановите доступ либо явно удалите ссылку перед подтверждением.");
  return {values, comment, attachments: attachments.map(item => ({document_id: item.document_id, version_id: item.version_id || null, block_id: item.block_id || null, file_id: item.file_id || null, note: item.note || ""}))};
}
