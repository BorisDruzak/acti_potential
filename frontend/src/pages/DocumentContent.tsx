import { useEffect, useRef, useState } from "react";
import { saveDownload } from "../api/client";
import { Feedback } from "../components/Feedback";
import { blockKinds, extractionErrors, extractionStates, extractionWarnings, type ContentBlock, type ContentExtraction, type ContentTarget } from "../engine/content";
import type { Card } from "../engine/schema";
import { useAction, useData } from "../engine/useData";

function BlockList({extraction, target, mode, documentId}: {extraction: ContentExtraction; target?: ContentTarget; mode: string; documentId: string}) {
  const [page, setPage] = useState(() => target?.fileId === extraction.file_id && target.ordinal !== undefined ? Math.floor(target.ordinal / 200) + 1 : 1);
  const remote = useData<{items: ContentBlock[]; total: number; page: number; page_size: number}>(page > 1 ? `/content/extractions/${extraction.id}/blocks?page=${page}&page_size=200&mode=${mode}` : null);
  const blocks = page > 1 ? remote.data?.items || [] : extraction.blocks;
  const total = extraction.block_count ?? extraction.blocks.length;
  useEffect(() => {
    if (!target?.blockId) return;
    document.getElementById(`block-${target.blockId}`)?.scrollIntoView?.({block: "center"});
  }, [target?.blockId, blocks]);
  if (!total) return null;
  return <>
    <div className="toolbar"><h4>Структура документа</h4><span className="muted">Блоков: {total}</span></div>
    <Feedback error={remote.error} loading={remote.loading}/>
    <div className="content-blocks">{blocks.map(block => <article key={block.id} id={`block-${block.id}`} className={`content-block${target?.blockId === block.id ? " selected" : ""}`}>
      <div className="toolbar"><small>{blockKinds[block.kind] || "Текст"}{block.page ? ` · страница ${block.page}` : ""} · блок {block.ordinal + 1}</small><a href={`/${mode === "viewer" ? "view" : "work"}?${new URLSearchParams({document: documentId, tab: "content", file: extraction.file_id, block: block.id, ordinal: String(block.ordinal)})}#block-${block.id}`} aria-label={`Ссылка на блок ${block.ordinal + 1}`}>#{block.anchor || block.ordinal + 1}</a></div>
      {Array.isArray(block.table) ? <div className="table-wrap"><table><tbody>{block.table.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, columnIndex) => <td key={columnIndex}>{cell}</td>)}</tr>)}</tbody></table></div> : <p className="content-text">{block.text}</p>}
    </article>)}</div>
    {total > 200 && <div className="pagination"><button type="button" disabled={page === 1 || remote.loading} onClick={() => setPage(current => current - 1)}>Предыдущие блоки</button><span>Страница блоков {page}</span><button type="button" disabled={page * 200 >= total || remote.loading} onClick={() => setPage(current => current + 1)}>Следующие блоки</button></div>}
  </>;
}

export function DocumentContent({card, readOnly, target}: {card: Card; readOnly: boolean; target?: ContentTarget}) {
  const mode = readOnly ? "viewer" : "editor";
  const [page, setPage] = useState(1);
  const [fileId, setFileId] = useState(target?.fileId || "");
  const content = useData<{items: ContentExtraction[]; total: number; page: number; page_size: number}>(`/documents/${card.id}/content?${new URLSearchParams({mode, page: String(page), page_size: "20", ...(fileId ? {file_id: fileId} : {})})}`);
  const action = useAction();
  const polls = useRef(0);
  const pending = !!content.data?.items.some(item => ["pending", "running"].includes(item.state));
  const canIdentifyMissing = !!content.data && (!!fileId || (page === 1 && content.data.total <= content.data.items.length));
  const missing = canIdentifyMissing ? card.files.filter(file => (!fileId || file.id === fileId) && !content.data?.items.some(item => item.file_id === file.id)) : [];
  const waitingForQueue = missing.length > 0;
  useEffect(() => {
    if ((!pending && !waitingForQueue) || polls.current >= 60) return;
    const timer = window.setTimeout(() => {polls.current += 1; content.reload();}, 4000);
    return () => window.clearTimeout(timer);
  }, [pending, waitingForQueue, content.data, content.reload]);
  const canExtract = !readOnly && card.permissions.includes("upload");
  async function extract(fileId: string) {
    await action.request(`/files/${fileId}/extract`, "POST", {});
    content.reload();
  }
  return <section className="panel document-content">
    <div className="toolbar"><div><h2>Содержание и источники</h2><p>Цифровой текст связан с точной версией оригинала. Результат обработки не изменяет файл.</p></div><button type="button" onClick={() => {polls.current = 0; content.reload();}}>Обновить содержание</button></div>
    <label>Файл для просмотра содержания<select value={fileId} onChange={event => {setFileId(event.target.value); setPage(1); polls.current = 0;}}><option value="">Все разрешённые оригиналы</option>{card.files.map(file => <option key={file.id} value={file.id}>{file.name} · версия {file.version}</option>)}</select></label>
    <Feedback error={content.error || action.error} message={action.message} loading={content.loading}/>
    {(pending || waitingForQueue) && <p role="status">{polls.current < 60 ? "Обработка выполняется в фоне. Состояние обновляется автоматически." : "Обработка ещё не завершена. Нажмите «Обновить содержание», чтобы проверить состояние."}</p>}
    {content.data?.items.map(extraction => <details key={extraction.id} className="extraction" open={target?.fileId === extraction.file_id || content.data?.items.length === 1}>
      <summary>{extraction.name} · версия {extraction.version} · {extractionStates[extraction.state] || "Состояние обработки неизвестно"}</summary>
      <div className="toolbar"><p className="muted">{extraction.parser} {extraction.parser_version}{extraction.completed_at ? " · " + new Date(extraction.completed_at).toLocaleString("ru-RU") : ""}</p><div className="button-row">
        {card.permissions.includes("download") && <button type="button" disabled={action.busy} onClick={() => void action.run(() => saveDownload(extraction.file_id, extraction.name, mode), "Оригинал скачан")}>Скачать оригинал версии {extraction.version}</button>}
        {canExtract && extraction.state === "failed" && extraction.attempts < 3 && <button type="button" disabled={action.busy} onClick={() => void action.run(() => extract(extraction.file_id), "Повторная обработка запрошена")}>Повторить извлечение</button>}
      </div></div>
      {extraction.state === "no_text" && <p>Нет цифрового текстового слоя. Распознавание сканов пока не подключено.</p>}
      {extraction.state === "encrypted" && <p>Файл защищён паролем. Для извлечения загрузите доступный оригинал новой версией.</p>}
      {extraction.state === "unsupported" && <p>{extractionErrors[extraction.error_code || ""] || "Формат не поддерживается. Оригинал остаётся доступен по вашим правам."}</p>}
      {extraction.state === "failed" && <p className="error" role="alert">{extractionErrors[extraction.error_code || ""] || "Не удалось обработать файл. Карточка и оригинал сохранены."}</p>}
      {canExtract && extraction.state === "failed" && extraction.attempts >= 3 && <p className="muted">Достигнут предел попыток обработки. Проверьте оригинал и при необходимости загрузите исправленную версию.</p>}
      {!!extraction.warnings.length && <ul className="content-warnings">{extraction.warnings.map((warning, index) => <li key={index}>{extractionWarnings[warning] || "При извлечении возникло предупреждение. Сверьте содержание с оригиналом."}</li>)}</ul>}
      {extraction.state === "completed" && <BlockList extraction={extraction} target={target?.fileId === fileId ? target : undefined} mode={mode} documentId={card.id}/>}
    </details>)}
    {!content.loading && !content.error && missing.map(file => <div className="file-line" key={file.id}><span>{file.name} · версия {file.version}<small>Содержание ещё не обработано</small></span>{canExtract && <button type="button" disabled={action.busy} onClick={() => void action.run(() => extract(file.id), "Файл поставлен в очередь обработки")}>Извлечь содержание</button>}</div>)}
    {content.data && <div className="pagination"><span>Результатов обработки: {content.data.total} · страница {page}</span><button type="button" disabled={page <= 1 || content.loading} onClick={() => {polls.current = 0; setPage(value => value - 1);}}>Предыдущее содержание</button><button type="button" disabled={page * content.data.page_size >= content.data.total || content.loading} onClick={() => {polls.current = 0; setPage(value => value + 1);}}>Следующее содержание</button></div>}
    {!card.files.length && <p className="empty-state">Загрузите оригинал документа, чтобы увидеть его содержание.</p>}
  </section>;
}

export function ContentErrorsAdmin() {
  const [page, setPage] = useState(1);
  const errors = useData<{items: (ContentExtraction & {document_id: string; document_title?: string})[]; total?: number}>(`/admin/content/errors?page=${page}&page_size=20`);
  return <><div className="toolbar"><div><h1>Обработка содержания</h1><p>Ошибки и предупреждения только для документов, доступных вашей учётной записи.</p></div><button onClick={errors.reload}>Обновить ошибки обработки</button></div>
    <Feedback {...errors}/>
    {errors.data?.items.map(item => <section className="panel" key={item.id}><h2>{item.name} · версия {item.version}</h2><p>{extractionStates[item.state] || "Ошибка обработки"}</p><p>{extractionErrors[item.error_code || ""] || "Подробности доступны в карточке источника."}</p><a href={`/work?document=${encodeURIComponent(item.document_id)}&tab=content&file=${encodeURIComponent(item.file_id)}`}>Открыть содержание документа</a></section>)}
    {errors.data?.items.length === 0 && <p className="empty-state">Ошибок обработки доступных документов нет.</p>}
    {errors.data?.total !== undefined && errors.data.total > 20 && <div className="pagination"><button disabled={page === 1 || errors.loading} onClick={() => setPage(current => current - 1)}>Назад</button><span>Страница {page}</span><button disabled={page * 20 >= errors.data.total || errors.loading} onClick={() => setPage(current => current + 1)}>Вперёд</button></div>}
  </>;
}
