import { useState } from "react";
import { saveDownload, saveExport } from "../api/client";
import { Feedback } from "../components/Feedback";
import type { KnowledgeTargets, PracticeCase, Template, TemplateVersion } from "../engine/knowledge";
import { DynamicForm } from "../engine/DynamicForm";
import type { ProcessVersion } from "../engine/processes";
import type { RelationAnchor } from "../engine/relations";
import type { Card, DocumentType, ReferenceSet, SchemaVersion, Values } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { PracticeEditor, TemplateEditor } from "./KnowledgeEditor";

function KnowledgeTargetView({value, registryId, readOnly}: {value: KnowledgeTargets; registryId: string; readOnly: boolean}) {
  const mode = readOnly ? "viewer" : "editor";
  const types = useData<DocumentType[]>("/document-types");
  const users = useData<{id: string; display_name: string}[]>("/users/options");
  const schema = useData<SchemaVersion>(value.form_schema_id ? `/form-schemas/${value.form_schema_id}?mode=${mode}` : null);
  const references = useData<ReferenceSet[]>(value.form_schema_id ? `/references?mode=${mode}` : null);
  const process = useData<ProcessVersion>(value.process_version_id ? `/process-versions/${value.process_version_id}?mode=${mode}` : null);
  const fields = schema.data?.schema.sections.flatMap(section => section.fields).filter(field => field.filterable && value.characteristics[field.key]) || [];
  const values = Object.fromEntries(fields.map(field => [field.key, field.type === "multi_reference" ? value.characteristics[field.key] : value.characteristics[field.key]?.[0]])) as Values;
  return <section className="knowledge-targets"><dl className="process-facts"><div><dt>Владелец</dt><dd>{users.data?.find(user => user.id === value.owner_id)?.display_name || "Автор записи"}</dd></div><div><dt>Тип документа</dt><dd>{types.data?.find(type => type.id === value.document_type_id)?.name || "Любой разрешённый тип"}</dd></div>{schema.data && <div><dt>Закреплённая форма</dt><dd>Версия {schema.data.version}</dd></div>}{process.data && <div><dt>Назначение в процессе</dt><dd>{process.data.metadata_snapshot?.name || "Процесс"} · версия {process.data.version}{value.step_key ? " · " + (process.data.steps.find(step => step.key === value.step_key)?.title || "Разрешённый шаг") : ""}</dd></div>}</dl>
    {value.process_restricted && <p>Привязка к процессу ограничена.</p>}{value.characteristics_restricted && <p>Недоступные характеристики скрыты.</p>}
    {fields.length > 0 && <DynamicForm schema={{sections: [{key: "characteristics", title: "Ключевые характеристики", fields}]}} values={values} onChange={() => {}} mode="view" displayMode={mode} roles={[mode]} references={references.data || []} registryId={registryId}/>}
    <Feedback error={schema.error || process.error}/>
  </section>;
}

type Basis = {restricted?: boolean; label?: string; document?: Card; anchor?: RelationAnchor & {version?: number}; files?: {id: string; name: string; version: number}[]; blocks?: {id: string; text: string}[]; note?: string};
export function KnowledgeBasis({path, readOnly}: {path: string; readOnly: boolean}) {
  const [open, setOpen] = useState(false);
  const mode = readOnly ? "viewer" : "editor";
  const data = useData<{items: Basis[]} | Basis>(open ? `${path}?mode=${mode}` : null);
  const action = useAction();
  const items = data.data ? "items" in data.data ? data.data.items : [data.data] : [];
  return <section className="knowledge-basis"><button type="button" onClick={() => setOpen(value => !value)}>Проверить основания знания</button>
    {open && <div className="panel"><Feedback error={data.error || action.error} loading={data.loading}/>{items.map((item, index) => item.restricted ? <p key={index}>Основание ограничено</p> : <article className="content-block" key={index}>
      <h4>{item.document?.title || "Разрешённое основание"}{item.anchor?.version ? ` · оригинал версии ${item.anchor.version}` : ""}</h4>{item.note && <p>{item.note}</p>}
      {item.blocks?.map(block => <blockquote className="content-text" key={block.id}>{block.text}</blockquote>)}
      {item.files?.map(file => <div className="file-line" key={file.id}><span>{file.name} · версия {file.version}</span><button type="button" onClick={() => void action.run(() => saveDownload(file.id, file.name, mode), "Оригинал скачан")}>Скачать основание версии {file.version}</button></div>)}
      {item.document && <a href={`/${readOnly ? "view" : "work"}?${new URLSearchParams({document: item.document.id, ...(item.anchor?.file_id ? {tab: "content", file: item.anchor.file_id, ...(item.anchor.block_id ? {block: item.anchor.block_id, ordinal: String(item.anchor.ordinal || 0)} : {})} : {})})}`}>Открыть документ знания</a>}
    </article>)}{data.data && items.length === 0 && <p>Основания не добавлены.</p>}</div>}
  </section>;
}

function KnowledgeAccess({path, visibility, lifecycle = "active", onSaved}: {path: string; visibility: string; lifecycle?: string; onSaved: () => void}) {
  const [open, setOpen] = useState(false);
  const [access, setAccess] = useState(visibility);
  const [state, setState] = useState(lifecycle);
  const [reason, setReason] = useState("");
  const action = useAction();
  return <section><button type="button" onClick={() => setOpen(value => !value)}>Изменить видимость и состояние</button>{open && <form className="panel compact-form" onSubmit={event => {event.preventDefault(); void action.run(async () => {await action.request(path + "/access", "PATCH", {visibility: access, lifecycle: state, reason}); onSaved(); setOpen(false);}, "Доступ к знанию изменён");}}><label>Новая видимость<select value={access} onChange={event => setAccess(event.target.value)}><option value="registry">Сотрудникам с доступом к реестру</option><option value="private">Личное: только автор и владелец</option></select></label><label>Состояние знания<select value={state} onChange={event => setState(event.target.value)}><option value="active">Действующее</option><option value="archived">Архивное</option></select></label><label>Причина изменения доступа<textarea required value={reason} onChange={event => setReason(event.target.value)}/></label><Feedback {...action}/><button disabled={action.busy || !reason.trim()}>Сохранить доступ к знанию</button><p>Опубликованное содержание и прежние версии сохраняются.</p></form>}</section>;
}

function CaseHistory({id, mode}: {id: string; mode: string}) {
  const [open, setOpen] = useState(false);
  const history = useData<{items: {id: string; action: string; reason: string; actor_name?: string; created_at: string}[]}>(open ? `/knowledge/cases/${id}/history?mode=${mode}` : null);
  const labels: Record<string, string> = {created: "Создан", updated: "Изменён", published: "Опубликован", confirmed: "Подтверждён", recommended: "Рекомендован", unrecommended: "Рекомендация снята", confirm: "Подтверждение", recommend: "Рекомендация", unrecommend: "Снятие рекомендации", access_changed: "Изменён доступ"};
  return <section><button type="button" onClick={() => setOpen(value => !value)}>История практики</button>{open && <><Feedback error={history.error} loading={history.loading}/><ul>{history.data?.items.map(item => <li key={item.id}><strong>{labels[item.action] || "Изменение знания"}</strong> · {new Date(item.created_at).toLocaleString("ru-RU")}{item.actor_name && ` · ${item.actor_name}`}<p>{item.reason}</p></li>)}</ul></>}</section>;
}

export function TemplateDetail({id, readOnly, onChanged}: {id: string; readOnly: boolean; onChanged?: () => void}) {
  const mode = readOnly ? "viewer" : "editor";
  const data = useData<Template>(`/knowledge/templates/${id}?mode=${mode}`);
  const versions = useData<TemplateVersion[]>(`/knowledge/templates/${id}/versions?mode=${mode}`);
  const [selected, setSelected] = useState("");
  const [editing, setEditing] = useState(false);
  const action = useAction();
  const current = versions.data?.find(version => version.id === selected) || data.data?.version;
  function reload() {data.reload(); versions.reload(); onChanged?.();}
  return <section className="knowledge-detail template-detail"><Feedback error={data.error || versions.error || action.error} loading={data.loading} message={action.message}/>
    {current && data.data && <><h2>{current.name} · версия {current.version}</h2><span className="badge">{current.state === "published" ? "Опубликован" : "Черновик"}</span><p>{current.visibility === "private" ? "Личное знание" : "Доступ по реестру"}</p>
      <label>Версия шаблона<select value={current.id} onChange={event => {setSelected(event.target.value); setEditing(false);}}>{versions.data?.map(version => <option key={version.id} value={version.id}>Версия {version.version} · {version.state === "published" ? "Опубликована" : "Черновик"}</option>)}</select></label>
      <p className="content-text">{current.purpose}</p><p>Категория: {current.category || "Не указана"}</p>
      <KnowledgeTargetView value={current} registryId={data.data.registry_id} readOnly={readOnly}/>
      {current.original.restricted ? <p>Оригинал ограничен</p> : <div className="file-line"><span>{current.original.name || "Закреплённый оригинал"} · версия оригинала {current.original.version}</span><button type="button" disabled={action.busy} onClick={() => void action.run(() => saveExport(`/knowledge/template-versions/${current.id}/download?mode=${mode}`, current.original.name || "template"), "Шаблон скачан")}>Скачать шаблон версии {current.version}</button></div>}
      <KnowledgeBasis key={current.id} path={`/knowledge/template-versions/${current.id}/basis`} readOnly={readOnly}/>
      {!readOnly && <div className="button-row">{current.state === "draft" && current.permissions.edit && <button type="button" onClick={() => setEditing(true)}>Редактировать шаблон</button>}{current.state === "draft" && current.permissions.publish && <button type="button" disabled={action.busy} onClick={() => void action.run(async () => {await action.request(`/knowledge/template-versions/${current.id}/publish`, "POST", {revision: current.revision}); reload();}, "Шаблон опубликован")}>Опубликовать шаблон</button>}{data.data.permissions.edit && <button type="button" disabled={action.busy} onClick={() => void action.run(async () => {const next = await action.request<TemplateVersion>(`/knowledge/templates/${id}/versions`, "POST", {}); setSelected(next.id); reload(); setEditing(true);}, "Новая версия шаблона создана")}>Новая версия шаблона</button>}</div>}
      {editing && !readOnly && <TemplateEditor key={current.id} version={current} registryId={data.data.registry_id} onSaved={reload} onClose={() => setEditing(false)}/>}
      {!readOnly && data.data.permissions.access && <KnowledgeAccess path={`/knowledge/templates/${id}`} visibility={current.visibility} lifecycle={data.data.lifecycle} onSaved={reload}/>}
    </>}
  </section>;
}

export function CaseDetail({id, readOnly, onChanged}: {id: string; readOnly: boolean; onChanged?: () => void}) {
  const mode = readOnly ? "viewer" : "editor";
  const data = useData<PracticeCase>(`/knowledge/cases/${id}?mode=${mode}`);
  const [editing, setEditing] = useState(false);
  const [decision, setDecision] = useState("");
  const [reason, setReason] = useState("");
  const action = useAction();
  function reload() {data.reload(); onChanged?.();}
  const value = data.data;
  return <section className="knowledge-detail case-detail"><Feedback error={data.error || action.error} loading={data.loading} message={action.message}/>{value && <>
    <h2>{value.name}</h2><p><span className="badge">{value.state === "published" ? "Опубликован" : "Черновик"}</span> · {value.recommended ? "Рекомендованный пример" : value.confirmed ? "Подтверждённый случай" : "Не подтверждён"} · {value.visibility === "private" ? "Личное знание" : "Доступ по реестру"}</p>
    <p className="muted">{value.author_name || "Автор случая"}{value.created_at ? " · " + new Date(value.created_at).toLocaleString("ru-RU") : ""} · {value.category || "Без категории"}</p>
    <h3>Как выполняли работу</h3><p className="content-text">{value.description}</p><h3>Результат</h3><p className="content-text">{value.result}</p>
    <KnowledgeTargetView value={value} registryId={value.registry_id} readOnly={readOnly}/>
    <KnowledgeBasis path={`/knowledge/cases/${id}/basis`} readOnly={readOnly}/>
    {!readOnly && <div className="button-row">{value.state === "draft" && value.permissions.edit && <button type="button" onClick={() => setEditing(true)}>Редактировать случай</button>}{value.state === "draft" && value.permissions.publish && <button type="button" disabled={action.busy} onClick={() => void action.run(async () => {await action.request(`/knowledge/cases/${id}/publish`, "POST", {revision: value.revision}); reload();}, "Случай опубликован")}>Опубликовать случай</button>}
      {value.state === "published" && value.permissions.curate && <>{!value.confirmed && <button type="button" onClick={() => setDecision("confirm")}>Подтвердить случай</button>}{value.confirmed && !value.recommended && <button type="button" onClick={() => setDecision("recommend")}>Рекомендовать пример</button>}{value.recommended && <button type="button" onClick={() => setDecision("unrecommend")}>Снять рекомендацию</button>}</>}
    </div>}
    {decision && !readOnly && <form className="panel compact-form" onSubmit={event => {event.preventDefault(); void action.run(async () => {await action.request(`/knowledge/cases/${id}/curate`, "POST", {action: decision, reason}); setDecision(""); setReason(""); reload();}, "Решение о практике сохранено");}}><label>Причина решения по практике<textarea required value={reason} onChange={event => setReason(event.target.value)}/></label><p>Решение сохраняется в истории. Опубликованное описание случая не изменяется.</p><button disabled={action.busy || !reason.trim()}>Сохранить решение по практике</button><button type="button" onClick={() => setDecision("")}>Отмена решения</button><Feedback {...action}/></form>}
    {editing && !readOnly && <PracticeEditor key={id + value.revision} value={value} onSaved={reload} onClose={() => setEditing(false)}/>}
    {!readOnly && value.permissions.access && <KnowledgeAccess path={`/knowledge/cases/${id}`} visibility={value.visibility} lifecycle={value.lifecycle} onSaved={reload}/>}
    <CaseHistory key={id + value.revision + value.confirmed + value.recommended} id={id} mode={mode}/>
  </>}</section>;
}
