import type { KeyboardEvent, MouseEvent } from "react";
import type { ContentHit, ContentTarget } from "../engine/content";
import type { Card, DocumentType, FormSchema } from "../engine/schema";
import { configuredColumnSort, configuredValue, legalActStatus, resultColumns, type DocumentColumn } from "../engine/documentPresentation";
import type { SelectionGesture } from "../engine/useDocumentSelection";
import { ContentMatches } from "./ContentMatches";

export function DocumentResults({results, schemas, sortSchemas, columnsSchema, types, roles, readOnly, selected, selectedIds = new Set(), query, sort, direction, typeId, onSort, onSelect, onOpen, onSelection, onContextMenu, onSelectAll}: {
  columnsSchema?: FormSchema; results: ContentHit[]; schemas: Map<string, FormSchema>; sortSchemas: FormSchema[]; types: DocumentType[]; roles: string[]; readOnly: boolean;
  selected: string; selectedIds?: Set<string>; query: string; sort: string; direction: string; typeId: string;
  onSort: (key: string) => void; onSelect: (id: string, target?: ContentTarget) => void; onOpen: (id: string) => void;
  onSelection?: (id: string, gesture: SelectionGesture) => void; onContextMenu?: (id: string, point: {x: number; y: number}) => void; onSelectAll?: () => void;
}) {
  const rows = results.map(result => "document" in result ? result.document : result as unknown as Card);
  const columns = resultColumns(columnsSchema ? [columnsSchema] : [...schemas.values()]);
  const columnWidth = (key: string) => ({reg_number: 100, reg_date: 112, card_state: 130, status: 140, document_type: 220}[key] || 160);
  const otherColumnsWidth = 42 + columns.filter(column => column.key !== "title").reduce((sum, column) => sum + columnWidth(column.key), 0);
  function value(item: Card, column: DocumentColumn) {
    if (column.key === "card_state") return item.card_state === "published" ? "Опубликован" : item.card_state === "draft" ? "Черновик" : "—";
    if (column.key === "status") return legalActStatus(item, schemas.get(item.schema_version_id), roles, readOnly);
    if (column.key === "document_type") return types.find(type => type.id === item.document_type_id)?.name || "—";
    if (column.key === "title" || column.key === "reg_number" || column.key === "reg_date") return item[column.key] || "—";
    return configuredValue(item, schemas.get(item.schema_version_id), column.label, roles, readOnly);
  }
  function sortKey(column: DocumentColumn): string | undefined {
    if (column.sort) return column.sort;
    if (!typeId || ["document_type", "card_state", "status"].includes(column.key)) return;
    return configuredColumnSort(sortSchemas, column.label);
  }
  function context(event: MouseEvent | KeyboardEvent, id: string) {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    onContextMenu?.(id, "clientX" in event ? {x: event.clientX, y: event.clientY} : {x: rect.left + 20, y: rect.top + 20});
  }
  function keydown(event: KeyboardEvent<HTMLTableRowElement>, index: number) {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter") {event.preventDefault(); onOpen(rows[index].id); return;}
    if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {context(event, rows[index].id); return;}
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {event.preventDefault(); onSelectAll?.(); return;}
    if (event.key === " ") {event.preventDefault(); onSelection?.(rows[index].id, {toggle: !event.shiftKey, range: event.shiftKey}); return;}
    const next = event.key === "ArrowDown" ? Math.min(rows.length - 1, index + 1) : event.key === "ArrowUp" ? Math.max(0, index - 1) : event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    if (event.shiftKey) onSelection?.(rows[next].id, {range: true, toggle: event.ctrlKey || event.metaKey});
    event.currentTarget.parentElement?.querySelectorAll<HTMLTableRowElement>("tr")[next].focus({preventScroll: true});
    event.currentTarget.parentElement?.querySelectorAll<HTMLTableRowElement>("tr")[next].scrollIntoView?.({block: "nearest"});
  }
  return <table className="document-results-table" aria-label="Документы" role="grid" aria-multiselectable="true"
    style={{minWidth: `calc(var(--document-title-width, 360px) + ${otherColumnsWidth}px)`}}>
    <colgroup><col style={{width: 42}}/>{columns.map(column => <col key={column.key} style={{width: column.key === "title" ? "var(--document-title-width, 360px)" : columnWidth(column.key)}}/>)}</colgroup>
    <thead><tr role="row"><th scope="col" role="columnheader" className="column-selection"><span className="sr-only">Выделение</span></th>{columns.map(column => {
      const key = sortKey(column);
      return <th key={column.key} scope="col" role="columnheader" className={`column-${column.key}${column.configured ? " column-configured" : ""}`}
        aria-sort={key && sort === key ? direction === "asc" ? "ascending" : "descending" : "none"}>
        {key ? <button type="button" onClick={() => onSort(key)} aria-label={`Сортировать: ${column.label}`} title={column.label}>
          {column.key === "reg_number" ? "Рег. номер" : column.label}<span aria-hidden="true">{sort === key ? direction === "asc" ? " ↑" : " ↓" : " ↕"}</span></button> : column.label}
      </th>;
    })}</tr></thead>
    <tbody>{rows.map((item, index) => <tr key={item.id} role="row" data-document-id={item.id} data-open-card={selected === item.id || undefined}
      aria-label={`Выбрать документ «${item.title || "Документ"}»${item.reg_number ? ". Номер: " + item.reg_number : ""}`}
      aria-selected={selectedIds.has(item.id)} tabIndex={selected === item.id || (!rows.some(row => row.id === selected) && index === 0) ? 0 : -1}
      onClick={event => {if (event.ctrlKey || event.metaKey || event.shiftKey) onSelection?.(item.id, {toggle: event.ctrlKey || event.metaKey, range: event.shiftKey}); else onSelect(item.id);}}
      onContextMenu={event => context(event, item.id)} onDoubleClick={() => onOpen(item.id)} onKeyDown={event => keydown(event, index)}>
      <td role="gridcell" className="column-selection"><input type="checkbox" aria-label={`Выделить документ «${item.title || "Документ"}»`} checked={selectedIds.has(item.id)}
        onChange={() => {}} onClick={event => {event.stopPropagation(); onSelection?.(item.id, {toggle: true, range: event.shiftKey});}} onDoubleClick={event => event.stopPropagation()}/></td>
      {columns.map(column => <td key={column.key} role="gridcell" className={`column-${column.key}${column.configured ? " column-configured" : ""}`} aria-label={`${column.label}: ${value(item, column)}`}>
        {column.key === "reg_number" && <span className="document-compact-label" aria-hidden="true">№ </span>}
        {column.key === "reg_date" && <span className="document-compact-label" aria-hidden="true">Дата: </span>}
        <span title={value(item, column)} className={column.key === "card_state" ? `document-state ${item.card_state || "unknown"}` : "document-cell-text"}>{value(item, column)}</span>
        {column.key === "title" && <div className="compact-document-meta"><span>{item.reg_number ? `№ ${item.reg_number}` : "Без номера"}</span><span>{item.reg_date ? new Date(item.reg_date + "T00:00:00").toLocaleDateString("ru-RU") : "Без даты"}</span><span className={`document-state ${item.card_state || "unknown"}`}>{item.card_state === "published" ? "Опубликован" : item.card_state === "draft" ? "Черновик" : "—"}</span></div>}
        {column.key === "title" && query && "document" in results[index] && <div className="document-result-snippets"
          onClick={event => {if ((event.target as HTMLElement).closest("button, a")) event.stopPropagation();}}
          onDoubleClick={event => {if ((event.target as HTMLElement).closest("button, a")) event.stopPropagation();}}>
          <ContentMatches matches={results[index].matches.slice(0, 2)} query={query} onOpen={target => onSelect(item.id, target)}/>
        </div>}
      </td>)}
    </tr>)}</tbody>
  </table>;
}
