import { useState } from "react";
import { saveDownload, saveExport } from "../api/client";
import { Feedback } from "../components/Feedback";
import type { RelationAnchor } from "../engine/relations";
import { useAction, useData } from "../engine/useData";

type Basis = {restricted?: boolean; label?: string; document?: {id: string; title: string}; anchor?: RelationAnchor & {version?: number}; files?: {id: string; name: string; version: number}[]; blocks?: {id: string; text: string}[]; note?: string; template?: {name: string; version: number; template_version_id: string}};
export function ExecutionBasis({instanceId, executionId, readOnly, kind}: {instanceId: string; executionId: string; readOnly: boolean; kind: "basis" | "templates" | "results"}) {
  const [open, setOpen] = useState(false);
  const mode = readOnly ? "viewer" : "editor";
  const data = useData<{items: Basis[]}>(open ? `/execution/instances/${instanceId}/steps/${executionId}/${kind}?mode=${mode}` : null);
  const action = useAction();
  const label = kind === "basis" ? "Основания шага дела" : kind === "templates" ? "Шаблоны шага дела" : "Документы подтверждённого результата";
  return <section className="execution-basis"><button type="button" onClick={() => setOpen(value => !value)}>{label}</button>{open && <div className="panel"><Feedback error={data.error || action.error} loading={data.loading} message={action.message}/>
    {data.data?.items.map((item, index) => item.restricted ? <p key={index}>{kind === "templates" ? "Шаблон ограничен" : "Документ ограничен"}</p> : <article key={index} className="content-block">
      <h4>{item.template ? `${item.template.name} · версия шаблона ${item.template.version}` : item.document?.title || "Разрешённый документ"}{item.anchor?.version ? ` · оригинал версии ${item.anchor.version}` : ""}</h4>
      {item.note && <p>{item.note}</p>}{item.blocks?.map(block => <blockquote key={block.id} className="content-text">{block.text}</blockquote>)}
      {item.template && <button type="button" disabled={action.busy} onClick={() => void action.run(() => saveExport(`/knowledge/template-versions/${item.template!.template_version_id}/download?mode=${mode}`, item.files?.[0]?.name || "template"), "Закреплённый шаблон скачан")}>Скачать шаблон дела версии {item.template.version}</button>}
      {item.files?.map(file => <div key={file.id} className="file-line"><span>{file.name} · версия {file.version}</span><button type="button" disabled={action.busy} onClick={() => void action.run(() => saveDownload(file.id, file.name, mode), "Оригинал скачан")}>Скачать оригинал дела версии {file.version}</button></div>)}
      {item.document && <a href={`/${readOnly ? "view" : "work"}?${new URLSearchParams({document: item.document.id, ...(item.anchor?.file_id ? {tab: "content", file: item.anchor.file_id, ...(item.anchor.block_id ? {block: item.anchor.block_id, ordinal: String(item.anchor.ordinal || 0)} : {})} : {})})}`}>Открыть документ дела</a>}
    </article>)}{data.data?.items.length === 0 && <p>Документы не добавлены.</p>}
  </div>}</section>;
}
