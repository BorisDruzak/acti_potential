import type { KeyboardEvent } from "react";
import type { ContentHit, ContentTarget } from "../engine/content";
import type { Card, DocumentType, FormSchema } from "../engine/schema";
import { configuredColumnSort, configuredValue, documentColumns, type DocumentColumn } from "../engine/documentPresentation";
import { ContentMatches } from "./ContentMatches";

export function DocumentResults({results, schemas, sortSchemas, types, roles, readOnly, selected, query, sort, direction, typeId, onSort, onSelect, onOpen}: {
  results: ContentHit[]; schemas: Map<string, FormSchema>; sortSchemas: FormSchema[]; types: DocumentType[]; roles: string[]; readOnly: boolean;
  selected: string; query: string; sort: string; direction: string; typeId: string;
  onSort: (key: string) => void; onSelect: (id: string, target?: ContentTarget) => void; onOpen: (id: string) => void;
}) {
  const rows = results.map(result => "document" in result ? result.document : result as unknown as Card);
  function value(item: Card, column: DocumentColumn) {
    if (column === "document_type") return types.find(type => type.id === item.document_type_id)?.name || "—";
    if (column === "title" || column === "reg_number" || column === "reg_date") return item[column] || "—";
    return configuredValue(item, schemas.get(item.schema_version_id), documentColumns.find(item => item.key === column)!.label, roles, readOnly);
  }
  function sortKey(column: typeof documentColumns[number]): string | undefined {
    if ("sort" in column) return column.sort;
    if (!typeId || column.key === "document_type") return;
    return configuredColumnSort(sortSchemas, column.label);
  }
  function keydown(event: KeyboardEvent<HTMLTableRowElement>, index: number) {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter") {event.preventDefault(); onOpen(rows[index].id); return;}
    if (event.key === " ") {event.preventDefault(); onSelect(rows[index].id); return;}
    const next = event.key === "ArrowDown" ? Math.min(rows.length - 1, index + 1) : event.key === "ArrowUp" ? Math.max(0, index - 1) : event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault(); onSelect(rows[next].id);
    event.currentTarget.parentElement?.querySelectorAll<HTMLTableRowElement>("tr")[next].focus({preventScroll: true});
    event.currentTarget.parentElement?.querySelectorAll<HTMLTableRowElement>("tr")[next].scrollIntoView?.({block: "nearest"});
  }
  return <table className="document-results-table" aria-label="Документы" role="grid">
    <thead><tr role="row">{documentColumns.map(column => {
      const key = sortKey(column);
      return <th key={column.key} scope="col" role="columnheader" className={`column-${column.key}`}
        aria-sort={key && sort === key ? direction === "asc" ? "ascending" : "descending" : "none"}>
        {key ? <button type="button" onClick={() => onSort(key)} aria-label={`Сортировать: ${column.label}`}>
          {column.label}<span aria-hidden="true">{sort === key ? direction === "asc" ? " ↑" : " ↓" : " ↕"}</span></button> : column.label}
      </th>;
    })}</tr></thead>
    <tbody>{rows.map((item, index) => <tr key={item.id} role="row" data-document-id={item.id}
      aria-label={`Выбрать документ «${item.title || "Документ"}»${item.reg_number ? ". Номер: " + item.reg_number : ""}`}
      aria-selected={selected === item.id} tabIndex={selected === item.id || (!rows.some(row => row.id === selected) && index === 0) ? 0 : -1}
      onClick={() => onSelect(item.id)} onDoubleClick={() => onOpen(item.id)} onKeyDown={event => keydown(event, index)}>
      {documentColumns.map(column => <td key={column.key} role="gridcell" className={`column-${column.key}`} aria-label={`${column.label}: ${value(item, column.key)}`}>
        {column.key === "reg_number" && <span className="document-compact-label" aria-hidden="true">№ </span>}
        {column.key === "reg_date" && <span className="document-compact-label" aria-hidden="true">Дата: </span>}
        <span>{value(item, column.key)}</span>
        {column.key === "title" && query && "document" in results[index] && <div className="document-result-snippets"
          onClick={event => {if ((event.target as HTMLElement).closest("button, a")) event.stopPropagation();}}
          onDoubleClick={event => {if ((event.target as HTMLElement).closest("button, a")) event.stopPropagation();}}>
          <ContentMatches matches={results[index].matches.slice(0, 2)} query={query} onOpen={target => onSelect(item.id, target)}/>
        </div>}
      </td>)}
    </tr>)}</tbody>
  </table>;
}
