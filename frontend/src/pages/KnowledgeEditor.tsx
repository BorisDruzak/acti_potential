import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { casePayload, emptyTargets, templatePayload, type PracticeCase, type TemplateVersion } from "../engine/knowledge";
import type { Registry } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { KnowledgeOriginalPicker, KnowledgeTargetFields } from "./KnowledgeFields";
import { ProcessSources } from "./ProcessSources";

const permissions = {edit: true, publish: true, curate: false};
export function TemplateEditor({version, registryId = "", onSaved, onClose}: {version?: TemplateVersion; registryId?: string; onSaved: (id: string) => void; onClose: () => void}) {
  const [draft, setDraft] = useState<TemplateVersion>(() => version ? structuredClone(version) : {...emptyTargets, id: "", template_id: "", version: 1, revision: 0, state: "draft", name: "", purpose: "", category: "", original: {}, permissions});
  const [registry, setRegistry] = useState(registryId);
  const registries = useData<Registry[]>("/registries");
  const action = useAction();
  function patch(next: Partial<TemplateVersion>) {setDraft(current => ({...current, ...next}));}
  return <form className="panel compact-form knowledge-editor" onSubmit={event => {event.preventDefault(); void action.run(async () => {
    const payload = templatePayload(draft);
    if (version) {await action.request(`/knowledge/template-versions/${version.id}`, "PUT", payload); onSaved(version.template_id);}
    else {const {revision: _revision, ...body} = payload; const saved = await action.request<{id: string}>("/knowledge/templates", "POST", {...body, registry_id: registry}); onSaved(saved.id);}
    onClose();
  }, "Шаблон сохранён");}}>
    <h2>{version ? `Редактирование шаблона · версия ${version.version}` : "Новый шаблон"}</h2>
    {!version && <label>Реестр знания<select required value={registry} onChange={event => setRegistry(event.target.value)}><option value="">Выберите реестр</option>{registries.data?.filter(item => item.permissions.includes("update") && item.permissions.includes("read")).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
    <label>Название шаблона<input required value={draft.name} onChange={event => patch({name: event.target.value})}/></label>
    <label>Назначение шаблона<textarea required value={draft.purpose} onChange={event => patch({purpose: event.target.value})}/></label>
    <label>Категория знания<input value={draft.category} onChange={event => patch({category: event.target.value})}/></label>
    <KnowledgeTargetFields value={draft} onChange={patch} registryId={registry} existing={!!version}/>
    <KnowledgeOriginalPicker value={draft.original} onChange={original => patch({original})}/>
    <Feedback error={action.error || registries.error} message={action.message}/>
    <div className="button-row"><button className="primary" disabled={action.busy || !draft.original.file_id}>Сохранить шаблон</button><button type="button" onClick={onClose}>Отмена редактирования знания</button></div>
  </form>;
}

export function PracticeEditor({value, registryId = "", onSaved, onClose}: {value?: PracticeCase; registryId?: string; onSaved: (id: string) => void; onClose: () => void}) {
  const [draft, setDraft] = useState<PracticeCase>(() => value ? structuredClone(value) : {...emptyTargets, id: "", registry_id: "", revision: 0, state: "draft", name: "", description: "", result: "", category: "", links: [], confirmed: false, recommended: false, permissions});
  const [registry, setRegistry] = useState(value?.registry_id || registryId);
  const registries = useData<Registry[]>("/registries");
  const action = useAction();
  function patch(next: Partial<PracticeCase>) {setDraft(current => ({...current, ...next}));}
  return <form className="panel compact-form knowledge-editor" onSubmit={event => {event.preventDefault(); void action.run(async () => {
    const payload = casePayload(draft);
    if (value) {await action.request(`/knowledge/cases/${value.id}`, "PUT", payload); onSaved(value.id);}
    else {const {revision: _revision, ...body} = payload; const saved = await action.request<{id: string}>("/knowledge/cases", "POST", {...body, registry_id: registry}); onSaved(saved.id);}
    onClose();
  }, "Случай сохранён");}}>
    <h2>{value ? "Редактирование случая" : "Новый случай практики"}</h2>
    {!value && <label>Реестр знания<select required value={registry} onChange={event => setRegistry(event.target.value)}><option value="">Выберите реестр</option>{registries.data?.filter(item => item.permissions.includes("update") && item.permissions.includes("read")).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
    <label>Название случая<input required value={draft.name} onChange={event => patch({name: event.target.value})}/></label>
    <label>Описание выполненного случая<textarea required rows={5} value={draft.description} onChange={event => patch({description: event.target.value})}/></label>
    <label>Результат случая<textarea required rows={4} value={draft.result} onChange={event => patch({result: event.target.value})}/></label>
    <label>Категория знания<input value={draft.category} onChange={event => patch({category: event.target.value})}/></label>
    <KnowledgeTargetFields value={draft} onChange={patch} registryId={registry} existing={!!value}/>
    <ProcessSources sources={draft.links} onChange={links => patch({links})}/>
    <p className="muted">Публикация сохраняет описание и результат. Подтверждение и рекомендация — отдельное решение уполномоченного сотрудника.</p>
    <Feedback error={action.error || registries.error} message={action.message}/>
    <div className="button-row"><button className="primary" disabled={action.busy}>Сохранить случай</button><button type="button" onClick={onClose}>Отмена редактирования знания</button></div>
  </form>;
}
