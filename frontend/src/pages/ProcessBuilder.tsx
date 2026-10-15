import { useState } from "react";
import { ProcessGraph } from "../components/ProcessGraph";
import { Feedback } from "../components/Feedback";
import { DynamicForm } from "../engine/DynamicForm";
import { moveItem, type DocumentType, type ReferenceSet, type SchemaVersion, type Values } from "../engine/schema";
import { newProcessStep, processPayload, type ProcessRecord, type ProcessStep, type ProcessVersion } from "../engine/processes";
import { useAction, useData } from "../engine/useData";
import { ProcessConditionEditor } from "./ProcessConditions";
import { ProcessSources } from "./ProcessSources";
import { ProcessView } from "./ProcessView";

function StepProperties({step, onChange, types}: {step: ProcessStep; onChange: (step: ProcessStep) => void; types: DocumentType[]}) {
  const [typeId, setTypeId] = useState("");
  const schema = useData<SchemaVersion>(step.form_schema_id ? `/form-schemas/${step.form_schema_id}?mode=editor` : null);
  const selectedType = typeId || schema.data?.document_type_id || "";
  const versions = useData<SchemaVersion[]>(selectedType ? `/document-types/${selectedType}/versions?mode=editor` : null);
  const patch = (next: Partial<ProcessStep>) => onChange({...step, ...next});
  return <div className="properties">
    <h3>Свойства шага</h3>
    <label>Название шага<input value={step.title} onChange={event => patch({title: event.target.value})}/></label>
    <label>Инструкция шага<textarea rows={5} value={step.instruction} onChange={event => patch({instruction: event.target.value})}/></label>
    <label>Исполнитель шага<input value={step.executor_role} onChange={event => patch({executor_role: event.target.value})}/></label>
    <label>Подразделение шага<input value={step.department} onChange={event => patch({department: event.target.value})}/></label>
    <label>Длительность, часов<input type="number" min="0" step="any" value={step.duration_hours ?? ""} onChange={event => patch({duration_hours: event.target.value ? Number(event.target.value) : null})}/></label>
    <label>Срок шага<input value={step.deadline} onChange={event => patch({deadline: event.target.value})}/></label>
    <label>Входные данные<textarea aria-label="Входные данные" value={step.inputs.join("\n")} onChange={event => patch({inputs: event.target.value.split("\n")})}/><small>Каждое требование с новой строки.</small></label>
    <label>Результаты шага<textarea aria-label="Результаты шага" value={step.outputs.join("\n")} onChange={event => patch({outputs: event.target.value.split("\n")})}/><small>Каждый результат с новой строки.</small></label>
    <label>Информационная система<input value={step.system} onChange={event => patch({system: event.target.value})}/></label>
    <label>Контрольное условие<textarea value={step.control_note} onChange={event => patch({control_note: event.target.value})}/></label>
    <label className="checkbox-label"><input type="checkbox" checked={step.is_start} onChange={event => patch({is_start: event.target.checked})}/>Начальный шаг</label>
    <label className="checkbox-label"><input type="checkbox" checked={step.is_terminal} onChange={event => patch({is_terminal: event.target.checked})}/>Завершающий шаг</label>
    <label>Тип формы шага<select value={selectedType} onChange={event => {setTypeId(event.target.value); patch({form_schema_id: null, form_restricted: false});}}><option value="">Без формы</option>{types.map(type => <option key={type.id} value={type.id}>{type.name}</option>)}</select></label>
    <label>Опубликованная форма процесса<select value={step.form_schema_id || ""} onChange={event => patch({form_schema_id: event.target.value || null, form_restricted: false})}><option value="">Не выбрана</option>{versions.data?.filter(version => version.state === "published").map(version => <option key={version.id} value={version.id}>Версия формы {version.version}</option>)}</select></label>
    <p className="muted">Версия формы закреплена в шаге. Новая публикация формы не изменит этот процесс.</p>
    <Feedback error={schema.error || versions.error}/>
    <ProcessSources sources={step.sources} onChange={sources => patch({sources})}/>
    <ProcessSources sources={step.templates} onChange={templates => patch({templates})} template/>
  </div>;
}

function PreviewAnswers({step, values, onChange, mode}: {step: ProcessStep; values: Values; onChange: (values: Values) => void; mode: "editor" | "viewer"}) {
  const schema = useData<SchemaVersion>(step.form_schema_id ? `/form-schemas/${step.form_schema_id}?mode=${mode}` : null);
  const references = useData<ReferenceSet[]>(`/references?mode=${mode}`);
  return <><Feedback error={schema.error}/>{schema.data && <DynamicForm schema={{...schema.data.schema, sections: schema.data.schema.sections.map(section => ({...section, fields: section.fields.map(field => ({...field, editable: true, write_roles: []}))}))}} values={values} onChange={onChange} mode="edit" displayMode={mode} roles={[mode]} references={references.data || []}/>}</>;
}

export function ProcessBuilder({process, version, types, onSaved}: {process: ProcessRecord; version: ProcessVersion; types: DocumentType[]; onSaved: () => void}) {
  const [draft, setDraft] = useState<ProcessVersion>(() => structuredClone(version));
  const [selected, setSelected] = useState(version.steps[0]?.key || "");
  const [transitionKey, setTransitionKey] = useState("");
  const [preview, setPreview] = useState<"editor" | "viewer" | null>(null);
  const [answers, setAnswers] = useState<Values>({});
  const [route, setRoute] = useState<string[]>([]);
  const [validated, setValidated] = useState(false);
  const action = useAction();
  const references = useData<ReferenceSet[]>("/references?mode=editor");
  const selectedStep = draft.steps.find(step => step.key === selected);
  const edge = draft.transitions.find(item => item.key === transitionKey);
  const edgeSource = draft.steps.find(step => step.key === edge?.from_step);
  const edgeSchema = useData<SchemaVersion>(edgeSource?.form_schema_id ? `/form-schemas/${edgeSource.form_schema_id}?mode=editor` : null);
  const published = version.state === "published";
  function selectStep(key: string) {setSelected(key); setTransitionKey(""); setAnswers({}); setRoute([]);}
  function openPreview(mode: "editor" | "viewer") {setPreview(mode); setAnswers({}); setRoute([]);}
  function change(next: ProcessVersion) {setDraft(next); setValidated(false);}
  function changeStep(next: ProcessStep) {change({...draft, steps: draft.steps.map(step => step.key === next.key ? next : next.is_start ? {...step, is_start: false} : step)});}
  async function persist() {
    const saved = await action.request<ProcessVersion>(`/process-versions/${draft.id}`, "PUT", processPayload(draft));
    setDraft(saved); return saved;
  }
  async function validate() {const saved = await persist(); await action.request(`/process-versions/${saved.id}/validate`, "POST", {revision: saved.revision}); setValidated(true);}
  function syntheticExample() {
    const titles = ["Определить маршрут", "Подготовить документ", "Подготовить закупку", "Уточнить исходные данные", "Завершить подготовку"];
    const steps = titles.map((title, index) => ({...newProcessStep(), title, instruction: ["Определите тип подготовки по опубликованной форме.", "Подготовьте проект документа по подтверждённому основанию.", "Подготовьте материалы закупки по проверенному регламенту.", "Уточните назначение и входные сведения.", "Проверьте результат и передайте его на следующий этап."][index], is_start: index === 0, is_terminal: index === 4, executor_role: "Ответственный сотрудник", inputs: ["Исходные сведения"], outputs: ["Результат подготовки"]}));
    const transition = (from: number, to: number, label: string, is_else = false) => ({key: "transition_" + crypto.randomUUID().slice(0, 8), from_step: steps[from].key, to_step: steps[to].key, label, condition: null, is_else});
    change({...draft, steps, transitions: [transition(0, 1, "Маршрут документа"), transition(0, 2, "Маршрут закупки"), transition(0, 3, "Уточнение", true), transition(1, 4, "Документ подготовлен"), transition(2, 4, "Материалы подготовлены"), transition(3, 4, "Сведения уточнены")]});
    setSelected(steps[0].key); setTransitionKey(""); action.setMessage("Создан синтетический черновик. Закрепите форму, два условия и реальные разрешённые основания перед публикацией.");
  }
  return <section className="process-builder">
    <div className="toolbar"><div><h2>Конструктор процесса</h2><p>Версия {draft.version} · {published ? "Опубликована, только чтение" : "Черновик"}</p></div><div className="button-row">
      <button onClick={() => openPreview("editor")}>Как редактор</button><button onClick={() => openPreview("viewer")}>Как читатель</button>
      {!published && <><button disabled={action.busy} onClick={() => void action.run(async () => {await persist();}, "Черновик процесса сохранён")}>Сохранить черновик процесса</button><button disabled={action.busy} onClick={() => void action.run(validate, "Проверка процесса пройдена")}>Проверить процесс</button><button className="primary" disabled={action.busy} onClick={() => void action.run(async () => {const saved = await persist(); await action.request(`/process-versions/${saved.id}/publish`, "POST", {revision: saved.revision}); onSaved();}, "Процесс опубликован")}>Опубликовать процесс</button></>}
    </div></div>
    <Feedback {...action}/>{validated && <p role="status">Структура, условия и доступ к основаниям проверены сервером.</p>}
    {preview && <section className="panel preview"><div className="toolbar"><h3>Предпросмотр процесса · {preview === "editor" ? "редактор" : "читатель"}</h3><button onClick={() => setPreview(null)}>Закрыть предпросмотр процесса</button></div>
      <p>Ответы используются только для проверки маршрута. Выполнение процесса и задачи не создаются.</p>
      <ProcessView process={process} version={draft} readOnly={preview === "viewer"} preview selectedKey={selected} onSelect={selectStep}/>
      {selectedStep && <><PreviewAnswers key={selected + preview} step={selectedStep} values={answers} onChange={values => {setAnswers(values); setRoute([]);}} mode={preview}/><button disabled={action.busy} onClick={() => void action.run(async () => {const saved = published ? draft : await persist(); const result = await action.request<{next_step_keys: string[]}>(`/process-versions/${saved.id}/preview?mode=${preview}`, "POST", {step_key: selected, values: answers}); setRoute(result.next_step_keys);}, "Маршрут проверен")}>Проверить маршрут</button>{route.length > 0 && <p role="status">Следующий шаг: {route.map(key => draft.steps.find(step => step.key === key)?.title || "Доступный шаг").join(", ")}</p>}</>}
    </section>}
    <div className="builder-grid">
      <div><ProcessGraph steps={draft.steps.map(step => ({...step, terminal: step.is_terminal}))} transitions={draft.transitions.map(item => ({key: item.key, from: item.from_step, to: item.to_step, label: item.label, otherwise: item.is_else, condition: item.condition}))} selected={selected} start={draft.steps.find(step => step.is_start)?.key || ""} onSelect={selectStep}/>
        <fieldset disabled={published || action.busy}>
          <div className="toolbar"><button type="button" onClick={() => {const step = newProcessStep(); if (!draft.steps.length) step.is_start = true; change({...draft, steps: [...draft.steps, step]}); setSelected(step.key);}}>+ Добавить шаг</button><button type="button" disabled={draft.steps.length > 0} onClick={syntheticExample}>Синтетический пример подготовки документа / закупки</button></div>
          <label>Дата начала действия версии<input type="date" value={draft.effective_from || ""} onChange={event => change({...draft, effective_from: event.target.value})}/></label>
          <label>Перейти к шагу<select value={selected} onChange={event => selectStep(event.target.value)}><option value="">Выберите шаг</option>{draft.steps.map((step, index) => <option key={step.key} value={step.key}>{index + 1}. {step.title}</option>)}</select></label>
          {selectedStep && <div className="button-row"><button disabled={draft.steps[0]?.key === selected} onClick={() => change({...draft, steps: moveItem(draft.steps, draft.steps.findIndex(step => step.key === selected), -1)})}>Шаг выше</button><button disabled={draft.steps.at(-1)?.key === selected} onClick={() => change({...draft, steps: moveItem(draft.steps, draft.steps.findIndex(step => step.key === selected), 1)})}>Шаг ниже</button><button className="danger-link" onClick={() => {change({...draft, steps: draft.steps.filter(step => step.key !== selected), transitions: draft.transitions.filter(edge => edge.from_step !== selected && edge.to_step !== selected)}); setSelected("");}}>Удалить шаг</button></div>}
          <h3>Переходы и ветвления</h3><p>Условия проверяются сверху вниз: используется первое совпадение, затем явный путь «Иначе».</p>
          {draft.transitions.map((item, index) => <div className="button-row" key={item.key}><button className={transitionKey === item.key ? "active" : ""} onClick={() => setTransitionKey(item.key)}>{index + 1}. {draft.steps.find(step => step.key === item.from_step)?.title || "?"} → {draft.steps.find(step => step.key === item.to_step)?.title || "?"}{item.is_else ? " · Иначе" : ""}</button><button aria-label={`Переход выше ${index + 1}`} disabled={index === 0} onClick={() => change({...draft, transitions: moveItem(draft.transitions, index, -1)})}>↑</button><button aria-label={`Переход ниже ${index + 1}`} disabled={index === draft.transitions.length - 1} onClick={() => change({...draft, transitions: moveItem(draft.transitions, index, 1)})}>↓</button><button aria-label={`Удалить переход ${index + 1}`} onClick={() => {change({...draft, transitions: draft.transitions.filter(edge => edge.key !== item.key)}); setTransitionKey("");}}>×</button></div>)}
          <button onClick={() => {const key = "transition_" + crypto.randomUUID().slice(0, 8); change({...draft, transitions: [...draft.transitions, {key, from_step: selected || draft.steps[0]?.key || "", to_step: "", label: "", condition: null, is_else: false}]}); setTransitionKey(key);}}>+ Добавить переход</button>
        </fieldset>
      </div>
      <aside className="property-panel"><fieldset disabled={published || action.busy}>
        {edge ? <div className="properties"><h3>Свойства перехода</h3><label>Из шага<select value={edge.from_step} onChange={event => change({...draft, transitions: draft.transitions.map(item => item.key === edge.key ? {...edge, from_step: event.target.value, condition: null} : item)})}>{draft.steps.map(step => <option key={step.key} value={step.key}>{step.title}</option>)}</select></label><label>В шаг<select value={edge.to_step} onChange={event => change({...draft, transitions: draft.transitions.map(item => item.key === edge.key ? {...edge, to_step: event.target.value} : item)})}><option value="">Выберите следующий шаг</option>{draft.steps.map(step => <option key={step.key} value={step.key}>{step.title}</option>)}</select></label><label>Название перехода<input value={edge.label} onChange={event => change({...draft, transitions: draft.transitions.map(item => item.key === edge.key ? {...edge, label: event.target.value} : item)})}/></label>
          <label>Правило перехода<select value={edge.restricted ? "restricted" : edge.is_else ? "else" : edge.condition ? "condition" : "always"} onChange={event => change({...draft, transitions: draft.transitions.map(item => item.key === edge.key ? {...edge, restricted: false, is_else: event.target.value === "else", condition: event.target.value === "condition" ? {field: "", op: "eq", value: ""} : null} : item)})}>{edge.restricted && <option value="restricted" disabled>Условие ограничено</option>}<option value="always">Без условия</option><option value="condition">По условию формы</option><option value="else">Иначе</option></select></label>
          {edge.condition && <ProcessConditionEditor schema={edgeSchema.data?.schema} condition={edge.condition} onChange={condition => change({...draft, transitions: draft.transitions.map(item => item.key === edge.key ? {...edge, condition} : item)})} references={references.data || []} registryId={process.registry_id}/>}
          <Feedback error={edgeSchema.error}/>
        </div> : selectedStep ? <StepProperties key={selectedStep.key} step={selectedStep} onChange={changeStep} types={types}/> : <p className="empty-state">Выберите шаг или переход для настройки.</p>}
      </fieldset></aside>
    </div>
  </section>;
}
