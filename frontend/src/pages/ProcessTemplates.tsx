import { useState } from "react";
import { Feedback } from "../components/Feedback";
import type { KnowledgePage, Template, TemplateVersion } from "../engine/knowledge";
import type { ProcessTemplate } from "../engine/processes";
import { useData } from "../engine/useData";

export function ProcessTemplates({value, onChange}: {value: ProcessTemplate[]; onChange: (next: ProcessTemplate[]) => void}) {
  const [query, setQuery] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [versionId, setVersionId] = useState("");
  const list = useData<KnowledgePage<Template>>(`/knowledge/templates?mode=editor&page_size=100&text=${encodeURIComponent(query)}`);
  const versions = useData<TemplateVersion[]>(templateId ? `/knowledge/templates/${templateId}/versions?mode=editor` : null);
  const version = versions.data?.find(item => item.id === versionId);
  return <fieldset><legend>Версии универсальных шаблонов</legend><ul>{value.map((item, index) => <li key={index}>{item.restricted ? "Шаблон ограничен" : `${item.name || "Шаблон"} · версия ${item.version || ""}`}<button type="button" aria-label={`Убрать универсальный шаблон ${index + 1}`} onClick={() => onChange(value.filter((_, position) => position !== index))}>Убрать</button></li>)}</ul>
    <label>Поиск универсального шаблона<input type="search" value={query} onChange={event => setQuery(event.target.value)}/></label>
    <label>Универсальный шаблон<select value={templateId} onChange={event => {setTemplateId(event.target.value); setVersionId("");}}><option value="">Выберите шаблон</option>{list.data?.items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>Закреплённая версия шаблона<select value={versionId} onChange={event => setVersionId(event.target.value)}><option value="">Выберите опубликованную версию</option>{versions.data?.filter(item => item.state === "published").map(item => <option key={item.id} value={item.id}>Версия {item.version} · {item.name}</option>)}</select></label>
    <button type="button" disabled={!version || value.some(item => item.template_version_id === version.id)} onClick={() => {if (version) onChange([...value, {template_version_id: version.id, name: version.name, version: version.version}]);}}>Закрепить версию шаблона</button>
    <p>Новая версия шаблона не меняет эту привязку. Опубликованные версии процесса сохраняются.</p><Feedback error={list.error || versions.error}/>
  </fieldset>;
}
