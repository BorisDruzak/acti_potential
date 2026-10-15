import { useDeferredValue, useState } from "react";
import { Feedback } from "../components/Feedback";
import type { KnowledgePage, PracticeCase, Template } from "../engine/knowledge";
import type { DocumentType, Registry } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { PracticeEditor, TemplateEditor } from "./KnowledgeEditor";
import { CaseDetail, TemplateDetail } from "./KnowledgeView";

type Selection = {kind: "templates" | "cases"; id: string};
function KnowledgeCards({templates = [], cases = [], onOpen}: {templates?: Template[]; cases?: PracticeCase[]; onOpen: (value: Selection) => void}) {
  return <div className="module-grid knowledge-cards">{templates.map(item => <button className="module-card" type="button" key={item.id} onClick={() => onOpen({kind: "templates", id: item.id})}><h3>{item.name}</h3><p>{item.purpose}</p><small>{item.category || "Без категории"} · {item.version?.state === "draft" ? "Черновик" : "Опубликован"}</small>{item.reasons?.length ? <p className="match-reasons">Почему подходит: {item.reasons.join("; ")}</p> : null}</button>)}
    {cases.map(item => <button className="module-card" type="button" key={item.id} onClick={() => onOpen({kind: "cases", id: item.id})}><h3>{item.name}</h3><p>{item.description}</p><small>{item.category || "Без категории"} · {item.recommended ? "Рекомендованный пример" : item.confirmed ? "Подтверждён" : "Не подтверждён"}</small>{item.reasons?.length ? <p className="match-reasons">Почему подходит: {item.reasons.join("; ")}</p> : null}</button>)}</div>;
}

function KnowledgeMetrics({registryId}: {registryId: string}) {
  const [open, setOpen] = useState(false);
  const data = useData<{items: {action: string; count: number}[]}>(open ? `/knowledge/metrics?registry_id=${encodeURIComponent(registryId)}` : null);
  const labels: Record<string, string> = {search: "Поиск подходящего знания", template_open: "Открытие шаблона", case_open: "Открытие случая", template_download: "Скачивание шаблона", basis_follow: "Проверка основания"};
  return <section><button type="button" onClick={() => setOpen(value => !value)}>Метрики использования знаний</button>{open && <div className="panel"><p>Сводные обращения к разрешённым знаниям без персональных отчётов.</p><Feedback error={data.error} loading={data.loading}/><ul>{data.data?.items.map(item => <li key={item.action}>{labels[item.action] || "Обращение к знанию"}: {item.count}</li>)}</ul></div>}</section>;
}

export function KnowledgeCatalog({kind, readOnly}: {kind: "templates" | "cases"; readOnly: boolean}) {
  const [query, setQuery] = useState("");
  const text = useDeferredValue(query);
  const [registryId, setRegistryId] = useState("");
  const [typeId, setTypeId] = useState("");
  const [category, setCategory] = useState("");
  const [recommended, setRecommended] = useState(false);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [creating, setCreating] = useState(false);
  const mode = readOnly ? "viewer" : "editor";
  const registries = useData<Registry[]>("/registries");
  const types = useData<DocumentType[]>("/document-types");
  const params = new URLSearchParams({mode, page: String(page), page_size: "20", ...(text ? {text} : {}), ...(registryId ? {registry_id: registryId} : {}), ...(typeId ? {document_type_id: typeId} : {}), ...(category ? {category} : {}), ...(recommended && kind === "cases" ? {recommended: "true"} : {})});
  const list = useData<KnowledgePage<Template | PracticeCase>>(`/knowledge/${kind}?${params}`);
  const action = useAction();
  const canCreate = !readOnly && registries.data?.some(registry => registry.permissions.includes("read") && registry.permissions.includes("update"));
  const registry = registries.data?.find(item => item.id === registryId);
  function open(value: Selection) {setSelected(value); setCreating(false); void action.run(async () => {await action.request("/knowledge/usage", "POST", value.kind === "templates" ? {action: "template_open", template_version_id: (list.data?.items.find(item => item.id === value.id) as Template | undefined)?.version?.id} : {action: "case_open", case_id: value.id});}, "");}
  return <section className="knowledge-catalog"><div className="toolbar"><div><h1>{kind === "templates" ? "Шаблоны" : "Как делали раньше"}</h1><p>{kind === "templates" ? "Версионные оригиналы, назначение и подтверждённые источники." : "Сохранённые случаи, результаты и проверяемые основания."}</p></div>{canCreate && <button className="primary" onClick={() => {setCreating(true); setSelected(null);}}>{kind === "templates" ? "Создать шаблон" : "Создать случай"}</button>}</div>
    <div className="panel filters"><label>Поиск знаний<input type="search" maxLength={255} placeholder="Метаданные и разрешённое содержание" value={query} onChange={event => {setQuery(event.target.value); setPage(1);}}/></label><label>Реестр знаний<select value={registryId} onChange={event => {setRegistryId(event.target.value); setPage(1); setSelected(null);}}><option value="">Все доступные реестры</option>{registries.data?.filter(registry => registry.permissions.includes("read")).map(registry => <option key={registry.id} value={registry.id}>{registry.name}</option>)}</select></label><label>Фильтр типа знания<select value={typeId} onChange={event => {setTypeId(event.target.value); setPage(1);}}><option value="">Все типы документов</option>{types.data?.map(type => <option key={type.id} value={type.id}>{type.name}</option>)}</select></label><label>Фильтр категории знания<input value={category} onChange={event => {setCategory(event.target.value); setPage(1);}}/></label>{kind === "cases" && <label className="checkbox-label"><input type="checkbox" checked={recommended} onChange={event => {setRecommended(event.target.checked); setPage(1);}}/>Только рекомендованные примеры</label>}</div>
    <Feedback error={list.error || registries.error || types.error || action.error} loading={list.loading}/>
    {creating && (kind === "templates" ? <TemplateEditor registryId={registryId} onSaved={id => {list.reload(); setSelected({kind, id});}} onClose={() => setCreating(false)}/> : <PracticeEditor registryId={registryId} onSaved={id => {list.reload(); setSelected({kind, id});}} onClose={() => setCreating(false)}/>)}
    {selected && <div className="panel"><button type="button" onClick={() => setSelected(null)}>К каталогу знаний</button>{selected.kind === "templates" ? <TemplateDetail key={selected.id} id={selected.id} readOnly={readOnly} onChanged={list.reload}/> : <CaseDetail key={selected.id} id={selected.id} readOnly={readOnly} onChanged={list.reload}/>}</div>}
    {!selected && !creating && <><KnowledgeCards templates={kind === "templates" ? list.data?.items as Template[] || [] : []} cases={kind === "cases" ? list.data?.items as PracticeCase[] || [] : []} onOpen={open}/>{list.data?.total === 0 && <p className="empty-state">Доступных знаний по этому запросу нет.</p>}{list.data && <div className="pagination"><span>Найдено: {list.data.total} · страница {page}</span><button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>Предыдущая страница знаний</button><button type="button" disabled={page * list.data.page_size >= list.data.total} onClick={() => setPage(value => value + 1)}>Следующая страница знаний</button></div>}</>}
    {registry?.permissions.includes("curate_knowledge") && <KnowledgeMetrics registryId={registryId}/>}
  </section>;
}

export type KnowledgeTab = "Шаблоны" | "Как делали раньше" | "Похожие случаи";
export const knowledgeTabs: KnowledgeTab[] = ["Шаблоны", "Как делали раньше", "Похожие случаи"];
export function RelatedKnowledge({documentId, processVersionId, stepKey, readOnly, activeTab}: {documentId?: string; processVersionId?: string; stepKey?: string; readOnly: boolean; activeTab?: KnowledgeTab}) {
  const [tab, setTab] = useState<KnowledgeTab>("Шаблоны");
  const [selected, setSelected] = useState<Selection | null>(null);
  const [typeId, setTypeId] = useState("");
  const current = activeTab || tab;
  const mode = readOnly ? "viewer" : "editor";
  const params = new URLSearchParams({mode, ...(documentId ? {document_id: documentId} : {}), ...(processVersionId ? {process_version_id: processVersionId, step_key: stepKey || ""} : {})});
  const data = useData<{templates: Template[]; cases: PracticeCase[]}>(`/knowledge/related?${params}`);
  const types = useData<DocumentType[]>("/document-types");
  const action = useAction();
  function open(value: Selection) {setSelected(value); const template = data.data?.templates.find(item => item.id === value.id); void action.run(async () => {await action.request("/knowledge/usage", "POST", value.kind === "templates" ? {action: "template_open", template_version_id: template?.version?.id} : {action: "case_open", case_id: value.id});}, "");}
  const cases = data.data?.cases.filter(item => !typeId || item.document_type_id === typeId) || [];
  const templates = data.data?.templates.filter(item => !typeId || item.document_type_id === typeId) || [];
  return <section className="related-knowledge"><h3>Знания для этой работы</h3>{!activeTab && <div className="tabs">{knowledgeTabs.map(name => <button type="button" key={name} className={current === name ? "active" : ""} onClick={() => {setTab(name); setSelected(null);}}>{name}</button>)}</div>}
    <p>{current === "Похожие случаи" ? "Подбор по разрешённым типам, категориям, форме, шагу и характеристикам. Сходство не означает рекомендацию." : current === "Как делали раньше" ? "Сохранённые случаи и результаты аналогичной работы. Уровень подтверждения указан у каждого примера." : "Подходящие опубликованные шаблоны и закреплённые версии."}</p>
    <label>Тип связанных знаний<select value={typeId} onChange={event => {setTypeId(event.target.value); setSelected(null);}}><option value="">Все разрешённые типы</option>{types.data?.map(type => <option key={type.id} value={type.id}>{type.name}</option>)}</select></label><Feedback error={data.error || action.error} loading={data.loading}/>
    {selected ? <div className="panel"><button type="button" onClick={() => setSelected(null)}>К подборке знаний</button>{selected.kind === "templates" ? <TemplateDetail id={selected.id} readOnly={readOnly}/> : <CaseDetail id={selected.id} readOnly={readOnly}/>}</div> : <><KnowledgeCards templates={current === "Шаблоны" ? templates : []} cases={current === "Шаблоны" ? [] : cases} onOpen={open}/>{!data.loading && (current === "Шаблоны" ? templates : cases).length === 0 && <p>Подходящих доступных знаний пока нет.</p>}</>}
  </section>;
}
