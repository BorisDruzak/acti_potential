import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { DynamicForm } from "../engine/DynamicForm";
import type { KnowledgeOriginal, KnowledgeTargets } from "../engine/knowledge";
import type { ProcessRecord, ProcessVersion } from "../engine/processes";
import type { Card, DocumentType, ReferenceSet, SchemaVersion, Values } from "../engine/schema";
import { useData } from "../engine/useData";
import type { RelationSelection, RelationVersion } from "../engine/relations";
import { RelationAnchorPicker } from "./RelationEditor";

export function KnowledgeTargetFields({value, onChange, registryId, existing = false}: {value: KnowledgeTargets; onChange: (next: KnowledgeTargets) => void; registryId: string; existing?: boolean}) {
  const users = useData<{id: string; display_name: string}[]>("/users/options");
  const types = useData<DocumentType[]>("/document-types");
  const forms = useData<SchemaVersion[]>(value.document_type_id ? `/document-types/${value.document_type_id}/versions?mode=editor` : null);
  const schema = useData<SchemaVersion>(value.form_schema_id ? `/form-schemas/${value.form_schema_id}?mode=editor` : null);
  const references = useData<ReferenceSet[]>("/references?mode=editor");
  const processes = useData<ProcessRecord[]>("/processes");
  const bound = useData<ProcessVersion>(value.process_version_id ? `/process-versions/${value.process_version_id}?mode=editor` : null);
  const [chosenProcess, setChosenProcess] = useState("");
  const processId = chosenProcess || bound.data?.process_id || "";
  const versions = useData<ProcessVersion[]>(processId ? `/processes/${processId}/versions?mode=editor` : null);
  function patch(next: Partial<KnowledgeTargets>) {onChange({...value, ...next});}
  const fields = schema.data?.schema.sections.flatMap(section => section.fields).filter(field => field.filterable && !["file", "group"].includes(field.type)) || [];
  const values = Object.fromEntries(fields.map(field => [field.key, field.type === "multi_reference" ? value.characteristics[field.key] || [] : value.characteristics[field.key]?.[0] ?? null])) as Values;
  return <fieldset><legend>Назначение и видимость</legend>
    {value.process_restricted && <p>Привязка к процессу ограничена. <button type="button" onClick={() => patch({process_restricted: false, process_version_id: null, step_key: null})}>Убрать недоступную привязку к процессу</button></p>}
    {value.characteristics_restricted && <p>Часть характеристик ограничена. <button type="button" onClick={() => patch({characteristics_restricted: false, characteristics: {}})}>Заменить недоступные характеристики</button></p>}
    <label>Владелец знания<select disabled={existing} value={value.owner_id || ""} onChange={event => patch({owner_id: event.target.value || null})}><option value="">Автор записи</option>{users.data?.map(user => <option key={user.id} value={user.id}>{user.display_name}</option>)}</select></label>
    <label>Видимость знания<select disabled={existing} value={value.visibility} onChange={event => patch({visibility: event.target.value as KnowledgeTargets["visibility"]})}><option value="registry">Сотрудникам с доступом к реестру</option><option value="private">Личное: только автор и владелец</option></select></label>
    {existing && <p className="muted">Владелец сохраняется. Видимость меняется отдельно действием «Изменить видимость и состояние» с указанием причины.</p>}
    <p className="muted">Видимость знания не предоставляет доступ к закрытым документам и файлам оснований.</p>
    <label>Тип документа знания<select value={value.document_type_id || ""} onChange={event => patch({document_type_id: event.target.value || null, form_schema_id: null, characteristics: {}, characteristics_restricted: false})}><option value="">Любой тип</option>{types.data?.map(type => <option key={type.id} value={type.id}>{type.name}</option>)}</select></label>
    <label>Форма знания<select value={value.form_schema_id || ""} onChange={event => patch({form_schema_id: event.target.value || null, characteristics: {}, characteristics_restricted: false})}><option value="">Без привязки к форме</option>{forms.data?.filter(form => form.state === "published").map(form => <option key={form.id} value={form.id}>Версия формы {form.version}</option>)}</select></label>
    <label>Процесс знания<select value={processId} onChange={event => {setChosenProcess(event.target.value); patch({process_version_id: null, step_key: null, process_restricted: false});}}><option value="">Без привязки к процессу</option>{processes.data?.map(process => <option key={process.id} value={process.id}>{process.name}</option>)}</select></label>
    <label>Версия процесса знания<select value={value.process_version_id || ""} onChange={event => patch({process_version_id: event.target.value || null, step_key: null})}><option value="">Выберите опубликованную версию</option>{versions.data?.filter(version => version.state === "published").map(version => <option key={version.id} value={version.id}>Версия процесса {version.version} · {version.effective_from}</option>)}</select></label>
    <label>Шаг процесса знания<select value={value.step_key || ""} onChange={event => patch({step_key: event.target.value || null})}><option value="">Весь процесс</option>{bound.data?.steps.map(step => <option key={step.key} value={step.key}>{step.title}</option>)}</select></label>
    {fields.length > 0 && <fieldset><legend>Ключевые характеристики</legend><DynamicForm schema={{sections: [{key: "characteristics", title: "Характеристики для подбора", fields: fields.map(field => ({...field, required: false, editable: true, write_roles: [], show_in_editor: true}))}]}} values={values} onChange={next => patch({characteristics: Object.fromEntries(Object.entries(next).flatMap(([key, entry]) => entry === null || entry === "" ? [] : [[key, (Array.isArray(entry) ? entry : [entry]).filter(item => ["string", "number", "boolean"].includes(typeof item)) as (string | number | boolean)[]]]))})} mode="edit" displayMode="editor" roles={["editor"]} references={references.data || []} registryId={registryId}/></fieldset>}
    <Feedback error={users.error || types.error || forms.error || processes.error || versions.error || bound.error || schema.error}/>
  </fieldset>;
}

export function KnowledgeOriginalPicker({value, onChange}: {value: KnowledgeOriginal; onChange: (next: KnowledgeOriginal) => void}) {
  const [query, setQuery] = useState("");
  const candidates = useData<{items: Card[]}>(`/documents?q=${encodeURIComponent(query)}&page_size=100&mode=editor`);
  const [selected, setSelected] = useState(value.document_id || "");
  const [anchor, setAnchor] = useState<RelationSelection>({version_id: value.version_id || "", block_id: null});
  const options = useData<{versions: RelationVersion[]}>(selected ? `/documents/${selected}/relation-options?mode=editor` : null);
  function choose(next: RelationSelection) {setAnchor(next); onChange({document_id: selected, version_id: next.version_id, file_id: ""});}
  return <fieldset><legend>Оригинал шаблона</legend>
    {value.restricted ? <p>Оригинал ограничен. Выберите доступный файл для новой редакции.</p> : value.name && <p>Закреплён: {value.name} · версия {value.version}</p>}
    <p>Выберите оригинал из разрешённого документа. Для нового файла создайте документ или загрузите следующую версию в его карточке, затем обновите список.</p>
    <a href="/work" target="_blank" rel="noreferrer">Создать документ или загрузить оригинал</a>
    <button type="button" onClick={() => {candidates.reload(); options.reload();}}>Обновить оригиналы</button>
    <label>Поиск документа шаблона<input type="search" value={query} onChange={event => setQuery(event.target.value)}/></label>
    <label>Документ шаблона<select value={selected} onChange={event => {setSelected(event.target.value); setAnchor({version_id: "", block_id: null}); onChange({document_id: event.target.value, version_id: "", file_id: ""});}}><option value="">Выберите разрешённый документ</option>{candidates.data?.items.map(card => <option value={card.id} key={card.id}>{card.title}</option>)}</select></label>
    {selected && <RelationAnchorPicker key={selected} documentId={selected} prefix="Оригинал шаблона" mode="editor" value={anchor} onChange={choose}/>}
    <label>Файл оригинала шаблона<select required value={value.file_id || ""} onChange={event => onChange({document_id: selected, version_id: anchor.version_id, file_id: event.target.value})}><option value="">Выберите точный оригинал</option>{options.data?.versions.find(version => version.id === anchor.version_id)?.files.map(file => <option key={file.id} value={file.id}>{file.name}</option>)}</select></label>
    <Feedback error={candidates.error || options.error}/>
  </fieldset>;
}
