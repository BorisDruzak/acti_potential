import { useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { Feedback } from "../components/Feedback";
import type { ReferenceItem, ReferenceSet, Registry } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { ReferenceItems, ReferenceValueEditor, ReferenceValuesTable, type ReferenceValue } from "./ReferenceItems";

function ReferenceCreate({registries, admin, onClose, onCreated}: {registries: Registry[]; admin: boolean; onClose: () => void; onCreated: (reference: ReferenceSet) => void}) {
  const [name,setName]=useState(""); const [description,setDescription]=useState(""); const [registry,setRegistry]=useState("");
  const [values,setValues]=useState<ReferenceItem[]>([]); const [editing,setEditing]=useState<ReferenceItem | null | undefined>(undefined);
  const action=useAction();
  async function saveValue(value: ReferenceValue) {
    if(values.some(item=>item.id!==editing?.id && item.label.trim().toLocaleLowerCase()===value.label.trim().toLocaleLowerCase())) throw new Error("Значение с таким наименованием уже добавлено");
    setValues(current=>editing ? current.map(item=>item.id===editing.id?{...item,...value}:item):[...current,{id:crypto.randomUUID(),...value}]);
    setEditing(undefined);
  }
  return <>
    <div className="reference-page-heading"><div><p className="reference-breadcrumb">Справочники <span aria-hidden="true">›</span> Создание</p><h1>Создание справочника</h1><p className="muted">Набор значений, используемых при заполнении карточек документов.</p></div><button onClick={onClose}>К списку справочников</button></div>
    <form className="reference-create-form" onSubmit={event=>{event.preventDefault();if(!registry)return;void action.run(async()=>{
      const created=await action.request<ReferenceSet>("/references","POST",{name:name.trim(),description,registry_id:registry==="global"?null:registry,initial_items:values.map(({label,description,active})=>({label,description,active}))});onCreated(created);
    });}}>
      <div className="reference-create-grid">
        <section className="panel reference-create-basics"><nav className="reference-steps" aria-label="Разделы создания справочника"><a href="#reference-basics"><span>1</span>Основные сведения</a><a href="#reference-draft-values"><span>2</span>Значения</a><a href="#reference-create-actions"><span>3</span>Проверка и создание</a></nav>
          <div id="reference-basics" className="reference-basics"><h2>Основные сведения</h2><p className="muted">Заполните основные параметры справочника.</p>
            <label><span>Реестр <span className="required-mark" aria-hidden="true">*</span></span><select aria-label="Реестр" required value={registry} onChange={event=>setRegistry(event.target.value)}><option value="" disabled>Выберите реестр</option>{admin&&<option value="global">Общий для всех реестров</option>}{registries.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
            <label>Название справочника<input required maxLength={255} value={name} onChange={event=>setName(event.target.value)} placeholder="Например, виды документов"/></label>
            <label>Описание справочника<textarea rows={5} maxLength={5000} value={description} onChange={event=>setDescription(event.target.value)} placeholder="Какие значения содержит справочник и где используется"/></label><span className="reference-character-count">{description.length}/5000</span>
          </div>
        </section>
        <section className="panel reference-values" id="reference-draft-values"><div className="reference-section-heading"><div><h2>Значения справочника</h2><p className="muted">Добавьте значения, которые будут доступны при заполнении карточек.</p></div><button type="button" className="primary" onClick={()=>setEditing(null)}>Добавить значение</button></div><ReferenceValuesTable items={values} participant={false} onEdit={setEditing} onRemove={item=>setValues(current=>current.filter(value=>value.id!==item.id))}/></section>
      </div>
      <Feedback {...action}/><div className="panel reference-create-actions" id="reference-create-actions"><button type="button" onClick={onClose} disabled={action.busy}>Отмена</button><span className="muted">{values.length ? `Значений к созданию: ${values.length}` : "Значения можно добавить после создания"}</span><button className="primary" disabled={action.busy || !name.trim() || !registry}>Создать справочник</button></div>
    </form>
    {editing!==undefined&&<ReferenceValueEditor key={editing?.id||"new"} participant={false} item={editing||undefined} onClose={()=>setEditing(undefined)} onSave={saveValue}/>}
  </>;
}

export function ReferencesWorkspace() {
  const auth=useAuth(); const admin=auth.user?.roles.includes("admin")??false;
  const references=useData<ReferenceSet[]>("/references?manage=true"); const registries=useData<Registry[]>("/registries");
  const [selected,setSelected]=useState<ReferenceSet|null>(null); const [creating,setCreating]=useState(false);
  const [query,setQuery]=useState(""); const [scope,setScope]=useState("");
  const writable=(registries.data||[]).filter(item=>admin||item.permissions?.includes("update"));
  const scopeName=(reference:ReferenceSet)=>(registries.data||[]).find(registry=>registry.id===reference.registry_id)?.name || (reference.registry_id ? "Отдельный реестр":"Общий для всех реестров");
  if(creating)return <div className="references-workspace"><ReferenceCreate admin={admin} registries={writable} onClose={()=>setCreating(false)} onCreated={value=>{setCreating(false);setSelected(value);references.reload();}}/></div>;
  if(selected)return <div className="references-workspace"><div className="reference-page-heading"><div><p className="reference-breadcrumb">Справочники <span aria-hidden="true">›</span> {scopeName(selected)}</p><h1>{selected.name}</h1></div><button onClick={()=>{setSelected(null);references.reload();}}>К списку справочников</button></div><ReferenceItems key={selected.id} reference={selected} onUpdated={value=>{setSelected(value);references.reload();}}/></div>;
  const filtered=(references.data||[]).filter(reference=>(!scope || (scope==="global"?!reference.registry_id:scope===reference.registry_id))&&[reference.name,reference.description,scopeName(reference)].join(" ").toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <div className="references-workspace"><div className="reference-page-heading"><div><span className="eyebrow">НАСТРОЙКА КАРТОЧЕК</span><h1>Справочники</h1><p className="muted">Общие и отдельные для каждого реестра наборы значений.</p></div><button className="primary" disabled={!admin&&!writable.length} onClick={()=>setCreating(true)}>Создать справочник</button></div>
    <section className="panel reference-catalog"><div className="reference-catalog-toolbar"><label>Поиск справочника<input type="search" placeholder="Название или описание" value={query} onChange={event=>setQuery(event.target.value)}/></label><label>Реестр<select value={scope} onChange={event=>setScope(event.target.value)}><option value="">Все реестры</option><option value="global">Общие справочники</option>{registries.data?.map(registry=><option key={registry.id} value={registry.id}>{registry.name}</option>)}</select></label><span className="muted">Найдено: {filtered.length}</span></div>
      <Feedback loading={references.loading} error={references.error||registries.error}/><div className="reference-table-scroll"><table className="reference-catalog-table"><thead><tr><th>Наименование</th><th>Реестр</th><th>Описание</th><th><span className="sr-only">Действия</span></th></tr></thead><tbody>{filtered.map(reference=><tr key={reference.id}><td><button className="reference-name-button" onClick={()=>setSelected(reference)}>{reference.name}</button>{reference.editable===false&&<small className="muted">Только просмотр</small>}</td><td><span className="reference-scope">{scopeName(reference)}</span></td><td className="reference-description">{reference.description||"—"}</td><td><button onClick={()=>setSelected(reference)} aria-label={`Открыть справочник: ${reference.name} · ${scopeName(reference)}`}>Открыть</button></td></tr>)}</tbody></table></div>
      {!references.loading&&!filtered.length&&<div className="reference-empty"><h3>{query||scope?"Справочники не найдены":"Здесь будут ваши справочники"}</h3><p>{query||scope?"Измените поисковый запрос или выберите другой реестр.":"Создайте справочник и добавьте значения для карточек документов."}</p></div>}
    </section>
  </div>;
}
