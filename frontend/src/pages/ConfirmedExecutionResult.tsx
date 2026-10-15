import { useAuth } from "../auth/AuthProvider";
import { Feedback } from "../components/Feedback";
import { DynamicForm } from "../engine/DynamicForm";
import type { ProcessInstance, StepResult } from "../engine/execution";
import type { ProcessStep } from "../engine/processes";
import type { Card, Field, ReferenceSet, Values } from "../engine/schema";
import { useData } from "../engine/useData";
import { ExecutionBasis } from "./ExecutionBasis";

function collectFiles(fields: Field[], values: Values, ids: Set<string>) {
  for (const field of fields) {
    const value = values[field.key];
    if (field.type === "file" && Array.isArray(value)) {
      for (const id of value) if (typeof id === "string") ids.add(id);
    } else if (field.type === "group" && Array.isArray(value)) {
      for (const row of value) if (row && typeof row === "object" && !Array.isArray(row)) collectFiles(field.children || [], row as Values, ids);
    }
  }
}
function withoutFileFields(fields: Field[]): Field[] {
  return fields.filter(field => field.type !== "file").map(field => field.type === "group" ? {...field, children: withoutFileFields(field.children || [])} : field);
}

export function ConfirmedExecutionResult({instance, executionId, result, step, readOnly, references}: {instance: ProcessInstance; executionId: string; result: StepResult; step: ProcessStep; readOnly: boolean; references: ReferenceSet[]}) {
  const auth = useAuth();
  const mode = readOnly ? "viewer" : "editor";
  const fileIds = new Set<string>();
  if (step.form_schema) collectFiles(step.form_schema.sections.flatMap(section => section.fields), result.values, fileIds);
  const sourceId = result.result_document?.restricted ? null : result.result_document?.document_id;
  const source = useData<Card>(sourceId ? `/documents/${sourceId}?mode=${mode}` : null);
  // The current card supplies only fresh ACL-filtered file metadata. Values and IDs
  // stay pinned to the confirmed result, including historical group row positions.
  const files = (source.data?.files || []).filter(file => fileIds.has(file.id)).map(file => ({...file, field_key: undefined}));
  const unresolved = !!result.result_document?.restricted || files.length < fileIds.size;
  const schema = step.form_schema && (unresolved ? {...step.form_schema, sections: step.form_schema.sections.map(section => ({...section, fields: withoutFileFields(section.fields)}))} : step.form_schema);
  return <section className="confirmed-step-result"><h4>Подтверждённый результат</h4><p>{new Date(result.created_at).toLocaleString("ru-RU")}{result.actor_name && ` · ${result.actor_name}`}</p>
    {result.result_document && <p>{result.result_document.restricted ? "Документ результата ограничен" : `${result.result_document.title || "Документ результата"} · редакция метаданных ${result.result_document.revision || ""}`}</p>}
    {schema && (!sourceId || source.data) && <DynamicForm schema={schema} values={result.values} mode="view" displayMode={mode} roles={auth.user?.roles || []} references={references} registryId={sourceId ? source.data?.registry_id : instance.registry_id} files={files} canDownload={source.data?.permissions.includes("download") || false}/>}
    {unresolved && <p className="muted">Оригиналы — в документах подтверждённого результата. Их доступ проверяется отдельно.</p>}
    {!!source.error && <p>Документ результата сейчас недоступен. Оригиналы можно проверить в документах подтверждённого результата.</p>}
    <Feedback loading={!!sourceId && !source.data && !source.error}/>
    <p className="content-text">{result.comment}</p><ExecutionBasis key={result.id} instanceId={instance.id} executionId={executionId} readOnly={readOnly} kind="results"/>
  </section>;
}
