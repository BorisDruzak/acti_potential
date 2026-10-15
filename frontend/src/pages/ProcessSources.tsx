import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { RelationAnchorPicker } from "./RelationEditor";
import type { ProcessSource } from "../engine/processes";
import type { RelationSelection, RelationVersion } from "../engine/relations";
import type { Card } from "../engine/schema";
import { useData } from "../engine/useData";

export function ProcessSources({sources, onChange, template = false}: {sources: ProcessSource[]; onChange: (sources: ProcessSource[]) => void; template?: boolean}) {
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [anchor, setAnchor] = useState<RelationSelection>({version_id: "", block_id: null});
  const [file, setFile] = useState("");
  const [note, setNote] = useState("");
  const candidates = useData<{items: Card[]}>(adding ? `/documents?q=${encodeURIComponent(query)}&page_size=100&mode=editor` : null);
  const options = useData<{versions: RelationVersion[]}>(template && documentId ? `/documents/${documentId}/relation-options?mode=editor` : null);
  const title = template ? "Шаблоны шага" : "Основания шага";
  const prefix = template ? "Шаблон шага" : "Основание шага";
  return <fieldset><legend>{title}</legend>
    <ul>{sources.map((source, index) => <li key={index}>{source.restricted ? "Основание ограничено" : source.note || `${prefix} ${index + 1}`}<button type="button" aria-label={`Удалить ${template ? "шаблон" : "основание"} ${index + 1}`} onClick={() => onChange(sources.filter((_, position) => position !== index))}>Убрать</button></li>)}</ul>
    <button type="button" onClick={() => setAdding(true)}>{template ? "+ Шаблон шага" : "+ Основание шага"}</button>
    {adding && <div className="panel compact-form">
      <label>Поиск документа основания<input type="search" value={query} onChange={event => setQuery(event.target.value)}/></label>
      <label>Документ основания<select value={documentId} onChange={event => {setDocumentId(event.target.value); setAnchor({version_id: "", block_id: null}); setFile("");}}><option value="">Выберите доступный документ</option>{candidates.data?.items.map(item => <option key={item.id} value={item.id}>{item.title || "Документ"}</option>)}</select></label>
      {documentId && <RelationAnchorPicker key={documentId} documentId={documentId} prefix={prefix} mode="editor" value={anchor} onChange={setAnchor}/>}
      {template && anchor.version_id && <label>Файл шаблона<select value={file} onChange={event => setFile(event.target.value)}><option value="">Выберите закреплённый оригинал</option>{options.data?.versions.find(version => version.id === anchor.version_id)?.files.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
      <label>Пояснение основания<textarea value={note} onChange={event => setNote(event.target.value)}/></label>
      <Feedback error={candidates.error || options.error}/>
      <div className="button-row"><button type="button" disabled={!documentId || !anchor.version_id || (template && !file)} onClick={() => {onChange([...sources, {document_id: documentId, version_id: anchor.version_id, block_id: anchor.block_id, ...(template ? {file_id: file} : {}), note}]); setAdding(false); setDocumentId(""); setAnchor({version_id: "", block_id: null}); setFile(""); setNote("");}}>Добавить {template ? "шаблон" : "основание"}</button><button type="button" onClick={() => setAdding(false)}>Отмена выбора основания</button></div>
    </div>}
  </fieldset>;
}
