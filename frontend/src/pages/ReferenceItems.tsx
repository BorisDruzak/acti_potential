import { useState } from "react";
import { encodeFile, saveExport } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { DocumentDialog } from "../components/DocumentDialog";
import { Feedback } from "../components/Feedback";
import type { ReferenceItem, ReferenceSet } from "../engine/schema";
import { useAction, useData } from "../engine/useData";

export const peopleCodes = new Set(["executors", "responsible_executors", "signers", "approvers"]);
const metadataText = (item: ReferenceItem | undefined, key: string) => typeof item?.metadata?.[key] === "string" ? String(item.metadata[key]) : "";
export type ReferenceValue = {label: string; description: string; active: boolean; metadata?: Record<string, unknown>};

export function ReferenceValueEditor({participant, item, onSave, onClose}: {
  participant: boolean; item?: ReferenceItem; onSave: (value: ReferenceValue) => Promise<void>; onClose: () => void;
}) {
  const [label, setLabel] = useState(item?.label || "");
  const [description, setDescription] = useState(item?.description || "");
  const [active, setActive] = useState(item?.active ?? true);
  const [position, setPosition] = useState(metadataText(item, "position"));
  const [department, setDepartment] = useState(metadataText(item, "department"));
  const action = useAction();
  return <DocumentDialog label={item ? "Редактирование значения" : "Новое значение справочника"} onClose={() => {if (!action.busy) onClose();}} className="reference-value-dialog">
    <h2>{item ? "Редактирование значения" : "Новое значение"}</h2><p className="muted">Название будет отображаться при заполнении карточки документа.</p>
    <form className="compact-form" onSubmit={event => {event.preventDefault(); void action.run(async () => {
      await onSave({label: label.trim(), description, active, ...(participant ? {metadata: {position, department}} : {})});
    });}}>
      <label>Наименование<input autoFocus required maxLength={255} value={label} onChange={event => setLabel(event.target.value)}/></label>
      {participant && <div className="form-grid"><label>Должность<input maxLength={255} value={position} onChange={event => setPosition(event.target.value)}/></label><label>Подразделение<input maxLength={255} value={department} onChange={event => setDepartment(event.target.value)}/></label></div>}
      <label>Описание значения<textarea maxLength={5000} rows={3} value={description} onChange={event => setDescription(event.target.value)}/></label>
      <label className="checkbox-label"><input type="checkbox" checked={active} onChange={event => setActive(event.target.checked)}/>Активно</label>
      <Feedback {...action}/><div className="reference-dialog-actions"><button type="button" disabled={action.busy} onClick={onClose}>Отмена</button><button className="primary" disabled={action.busy || !label.trim()}>{item ? "Сохранить значение" : "Добавить значение"}</button></div>
    </form>
  </DocumentDialog>;
}

export function ReferenceValuesTable({items, participant, onEdit, onRemove}: {
  items: ReferenceItem[]; participant: boolean; onEdit?: (item: ReferenceItem) => void; onRemove?: (item: ReferenceItem) => void;
}) {
  if (!items.length) return <div className="reference-empty"><span className="eyebrow">ЗНАЧЕНИЯ СПРАВОЧНИКА</span><h3>Добавьте первое значение</h3><p>Оно будет доступно для выбора при заполнении карточек документов.</p></div>;
  return <div className="reference-table-scroll"><table className="reference-values-table"><thead><tr><th>№</th><th>Наименование</th><th>{participant ? "Должность и подразделение" : "Описание"}</th><th>Статус</th><th><span className="sr-only">Действия</span></th></tr></thead><tbody>{items.map((item,index) => <tr key={item.id}>
    <td className="muted">{index + 1}</td><td><strong>{item.label}</strong></td><td className="reference-description">{participant ? [metadataText(item,"position"),metadataText(item,"department"),item.description].filter(Boolean).join(" · ") || "—" : item.description || "—"}</td><td><span className={`reference-status ${!item.active ? "reference-status-inactive" : ""}`}>{item.active ? item.selectable === false ? "Вне срока действия" : "Активно" : "Неактивно"}</span></td><td><div className="reference-row-actions">{onEdit && <button type="button" aria-label={`Изменить: ${item.label}`} onClick={() => onEdit(item)}>Изменить</button>}{onRemove && <button type="button" aria-label={`Убрать: ${item.label}`} onClick={() => onRemove(item)}>Убрать</button>}</div></td>
  </tr>)}</tbody></table></div>;
}

export function ReferenceItems({reference, onUpdated}: {reference: ReferenceSet; onUpdated?: (value: ReferenceSet) => void}) {
  const auth = useAuth(); const admin = auth.user?.roles.includes("admin") ?? false;
  const editable = reference.editable ?? admin;
  const participant = peopleCodes.has(reference.code);
  const items = useData<ReferenceItem[]>(`/references/${reference.id}/items`);
  const [historyOpen, setHistoryOpen] = useState(false);
  const history = useData<{items: {id: string; action: string; occurred_at: string}[]}>(historyOpen && editable ? `/references/${reference.id}/history` : null);
  const [name, setName] = useState(reference.name), [description, setDescription] = useState(reference.description || "");
  const [editing, setEditing] = useState<ReferenceItem | null | undefined>(undefined);
  const [query,setQuery] = useState(""); const action = useAction();
  return <div className="reference-editor-grid">
    <section className="panel reference-settings"><span className="eyebrow">СПРАВОЧНИК</span><h2>Основные сведения</h2><p className="muted">Название и описание помогут найти нужный набор значений.</p>
      <form className="compact-form" onSubmit={event => {event.preventDefault(); void action.run(async () => {const value=await action.request<ReferenceSet>(`/references/${reference.id}`,"PATCH",{name:name.trim(),description});onUpdated?.(value);},"Настройки сохранены");}}>
        <label>Название справочника<input required maxLength={255} disabled={!editable} value={name} onChange={event=>setName(event.target.value)}/></label>
        <label>Описание справочника<textarea maxLength={5000} rows={5} disabled={!editable} value={description} onChange={event=>setDescription(event.target.value)}/></label>
        {editable && <button className="primary" disabled={action.busy || !name.trim() || (name===reference.name && description===(reference.description || ""))}>Сохранить настройки</button>}
      </form><Feedback {...action}/>{!editable && <p className="muted">Справочник доступен для просмотра. Общие справочники изменяет администратор.</p>}
      {admin && <details className="reference-transfer"><summary>Обмен данными</summary><div className="button-row">{(["csv","xlsx"] as const).map(format=><button key={format} disabled={action.busy} onClick={()=>void action.run(()=>saveExport(`/references/${reference.id}/export?format=${format}`,`reference.${format}`))}>Экспорт {format.toUpperCase()}</button>)}</div>
        <label>Импорт CSV / XLSX<input type="file" accept=".csv,.xlsx" disabled={action.busy} onChange={event=>{const file=event.target.files?.[0];if(file)void action.run(async()=>{if(file.size>2*1024*1024)throw new Error("Файл превышает 2 МиБ");await action.request(`/references/${reference.id}/import`,"POST",{format:file.name.toLowerCase().endsWith(".xlsx")?"xlsx":"csv",content:await encodeFile(file)});items.reload();history.reload();},"Справочник импортирован");event.target.value="";}}/></label>
        <p className="muted">Используйте выгрузку справочника как шаблон для импорта. Существующие идентификаторы сохраняются.</p>
      </details>}
    </section>
    <section className="panel reference-values"><div className="reference-section-heading"><div><h2>Значения справочника</h2><p className="muted">{items.data?.length || 0} значений · используются в карточках документов</p></div>{editable && <button className="primary" onClick={()=>setEditing(null)}>Добавить значение</button>}</div>
      <label className="reference-search">Поиск по значениям<input type="search" placeholder="Наименование или описание" value={query} onChange={event=>setQuery(event.target.value)}/></label>
      <Feedback error={items.error} loading={items.loading}/>{!items.loading && <ReferenceValuesTable items={(items.data || []).filter(item=>[item.label,item.description,metadataText(item,"position"),metadataText(item,"department")].join(" ").toLocaleLowerCase().includes(query.toLocaleLowerCase()))} participant={participant} onEdit={editable ? setEditing : undefined}/>}
      {editable && <details className="reference-history" open={historyOpen} onToggle={event=>setHistoryOpen(event.currentTarget.open)}><summary>Журнал изменений</summary><Feedback {...history}/>{history.data?.items.map(item=><p key={item.id}>{new Date(item.occurred_at).toLocaleString("ru-RU")} · {({"reference.item.created":"Добавлено значение","reference.item.updated":"Изменено значение","reference.updated":"Обновлены сведения","reference.created":"Создан справочник"} as Record<string,string>)[item.action] || "Изменения справочника"}</p>)}</details>}
    </section>
    {editing !== undefined && <ReferenceValueEditor key={editing?.id || "new"} participant={participant} item={editing || undefined} onClose={()=>setEditing(undefined)} onSave={async value=>{const metadata=value.metadata && admin ? {...(editing?.metadata || {}),...value.metadata}:value.metadata;await action.request(`/references/${reference.id}/items${editing ? "/"+editing.id:""}`,editing?"PATCH":"POST",{...value,...(metadata?{metadata}:{})});setEditing(undefined);items.reload();history.reload();}}/>}
  </div>;
}
