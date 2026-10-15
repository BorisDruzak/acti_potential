import { useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { Feedback } from "../components/Feedback";
import { DynamicForm } from "../engine/DynamicForm";
import type { ProcessInstance, StepExecution } from "../engine/execution";
import { resultPayload } from "../engine/execution";
import type { ProcessStep, ProcessSource } from "../engine/processes";
import { editableValues, initialValues, type Card, type ReferenceSet, type Values } from "../engine/schema";
import { useData } from "../engine/useData";
import { ProcessSources } from "./ProcessSources";

export function ExecutionResultEditor({instance, execution, step, busy, onSave}: {instance: ProcessInstance; execution: StepExecution; step: ProcessStep; busy: boolean; onSave: (body: unknown) => Promise<void>}) {
  const auth = useAuth();
  const references = useData<ReferenceSet[]>("/references?mode=editor");
  const [values, setValues] = useState<Values>(() => execution.result?.values || (step.form_schema ? initialValues(step.form_schema) : {}));
  const [comment, setComment] = useState(execution.result?.comment || "");
  const [attachments, setAttachments] = useState<ProcessSource[]>(execution.result?.attachments || []);
  const [method, setMethod] = useState("inline");
  const [query, setQuery] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [error, setError] = useState("");
  const documents = useData<{items: Card[]}>(method === "document" ? `/documents?q=${encodeURIComponent(query)}&page_size=100&mode=editor` : null);
  const document = useData<Card>(documentId && method === "document" ? `/documents/${documentId}?mode=editor` : null);
  const compatible = !!document.data && document.data.schema_version_id === step.form_schema_id;
  return <form className="execution-result-editor compact-form" onSubmit={event => {event.preventDefault(); setError(""); void (async () => {
    try {
      const visibleValues = step.form_schema ? editableValues(step.form_schema, values, "edit", auth.user?.roles || []) : values;
      const body = resultPayload(method === "document" ? {} : visibleValues, comment, attachments);
      await onSave({...body, ...(method === "document" && compatible ? {result_document: {document_id: document.data!.id, revision: document.data!.revision}} : {})});
    } catch (failure) {setError(failure instanceof Error ? failure.message : "Не удалось подтвердить результат");}
  })();}}>
    <h4>Результат текущего шага</h4>
    {step.form_schema_id && <label>Способ заполнения результата<select value={method} onChange={event => setMethod(event.target.value)}><option value="inline">Ввести ответы шага</option><option value="document">Использовать документ результата</option></select></label>}
    {method === "inline" ? step.form_restricted ? <p>Форма результата ограничена. Для подтверждения нужен доступ к полям формы.</p> : step.form_schema && <DynamicForm schema={step.form_schema} values={values} onChange={setValues} mode="edit" displayMode="editor" roles={auth.user?.roles || []} references={references.data || []} registryId={instance.registry_id}/> : <fieldset><legend>Подтверждённый документ результата</legend>
      <p>Создайте или измените обычную карточку по закреплённой форме: загрузите оригиналы и присвойте номер, если он требуется. Подтверждение закрепит её текущие сведения и версию.</p>
      <label>Поиск карточки результата<input type="search" value={query} onChange={event => setQuery(event.target.value)}/></label>
      <label>Карточка результата<select required value={documentId} onChange={event => setDocumentId(event.target.value)}><option value="">Выберите разрешённую карточку</option>{documents.data?.items.map(card => <option key={card.id} value={card.id}>{card.title}</option>)}</select></label>
      {document.data && <p>{compatible ? `Совместимая форма · редакция метаданных ${document.data.revision} · версия оригиналов ${document.data.current_version}` : "Форма документа должна точно совпадать с закреплённой формой шага."}</p>}
      <button type="button" onClick={() => {document.reload(); documents.reload();}}>Обновить карточку результата</button>
    </fieldset>}
    <a href="/work" target="_blank" rel="noreferrer">Создать документ или загрузить файл результата</a>
    <p className="muted">Файлы и управляемые поля заполняются в обычной карточке. Для использования её значений выберите «Использовать документ результата».</p>
    <ProcessSources result sources={attachments} onChange={setAttachments}/>
    <label>Комментарий к результату<textarea rows={4} value={comment} onChange={event => setComment(event.target.value)}/></label>
    <p>Подтверждение сохраняет отдельный результат. Следующий шаг выбирается сервером при завершении текущего шага.</p>
    <Feedback error={error || references.error || documents.error || document.error}/>
    <button className="primary" disabled={busy || step.form_restricted || (method === "document" && !compatible)}>Подтвердить результат шага</button>
  </form>;
}
