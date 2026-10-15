import type { Condition, FormSchema } from "./schema";
export type ProcessSource = {document_id?: string; version_id?: string | null; block_id?: string | null; file_id?: string | null; note?: string; restricted?: boolean; label?: string};
export type ProcessStep = {
  key: string; title: string; instruction: string; executor_role: string; department: string;
  duration_hours: number | null; deadline: string; inputs: string[]; outputs: string[];
  system: string; control_note: string; is_start: boolean; is_terminal: boolean;
  form_schema_id?: string | null; form_schema?: FormSchema | null;
  form_restricted?: boolean;
  sources: ProcessSource[]; templates: ProcessSource[];
};
export type ProcessTransition = {key: string; from_step: string; to_step: string; label: string; condition: Condition | null; is_else: boolean; restricted?: boolean; ordinal?: number};
export type ProcessRecord = {id: string; registry_id: string; code: string; name: string; description: string; owner_id: string | null; department: string; category: string; purpose: string; status: string};
export type ProcessVersion = {
  id: string; process_id: string; version: number; state: string; effective_from: string; revision: number;
  metadata_snapshot?: Partial<ProcessRecord> | null; steps: ProcessStep[]; transitions: ProcessTransition[];
};
export function newProcessStep(): ProcessStep {
  return {key: "step_" + crypto.randomUUID().replaceAll("-", "").slice(0, 12), title: "Новый шаг", instruction: "", executor_role: "", department: "", duration_hours: null, deadline: "", inputs: [], outputs: [], system: "", control_note: "", is_start: false, is_terminal: false, form_schema_id: null, sources: [], templates: []};
}
export function processPayload(version: ProcessVersion) {
  if (version.steps.some(step => step.form_restricted || [...step.sources, ...step.templates].some(source => source.restricted)) || version.transitions.some(transition => transition.restricted))
    throw new Error("В черновике есть недоступные основания или условия. Восстановите доступ либо явно удалите недоступную ссылку перед сохранением.");
  const source = (item: ProcessSource) => ({document_id: item.document_id, version_id: item.version_id || null, block_id: item.block_id || null, note: item.note || ""});
  return {revision: version.revision, effective_from: version.effective_from,
    steps: version.steps.map(step => ({key: step.key, title: step.title, instruction: step.instruction, executor_role: step.executor_role, department: step.department, duration_hours: step.duration_hours ?? null, deadline: step.deadline, inputs: step.inputs, outputs: step.outputs, system: step.system, control_note: step.control_note, is_start: step.is_start, is_terminal: step.is_terminal, form_schema_id: step.form_schema_id || null, sources: step.sources.map(source), templates: step.templates.map(item => ({...source(item), file_id: item.file_id || null}))})),
    transitions: version.transitions.map(item => ({key: item.key, from_step: item.from_step, to_step: item.to_step, label: item.label, condition: item.condition, is_else: item.is_else}))};
}
