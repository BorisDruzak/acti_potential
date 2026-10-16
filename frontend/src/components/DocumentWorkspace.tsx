import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import "./document-workspace.css";

export type DocumentLayoutMode = "table" | "split" | "card";
type Layout = {mode: DocumentLayoutMode; returnTo: Exclude<DocumentLayoutMode, "card">};
const modes = [{mode: "table", label: "Таблица"}, {mode: "split", label: "Вместе"}, {mode: "card", label: "Карточка"}] as const;
function preference(key: string): Layout {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    if (modes.some(item => item.mode === value?.mode)) return {mode: value.mode, returnTo: value.returnTo === "table" ? "table" : "split"};
  } catch { /* Storage is optional. */ }
  return {mode: "split", returnTo: "split"};
}
export function useDocumentLayout(owner: string, readOnly: boolean) {
  const key = `acti.documents.layout.v1.${owner}.${readOnly ? "viewer" : "editor"}`;
  const [state, setState] = useState(() => ({key, ...preference(key)}));
  const layout = state.key === key ? state : {key, ...preference(key)};
  useEffect(() => {
    if (state.key !== key) {setState({key, ...preference(key)}); return;}
    try {localStorage.setItem(key, JSON.stringify({mode: state.mode, returnTo: state.returnTo}));} catch { /* Keep the current layout in memory. */ }
  }, [state, key]);
  function choose(mode: DocumentLayoutMode) {
    setState(current => {
      const previous = current.key === key ? current : {key, ...preference(key)};
      return {key, mode, returnTo: mode === "card" ? previous.mode === "card" ? previous.returnTo : previous.mode : mode};
    });
  }
  return {layout, choose, back: () => choose(layout.mode === "card" ? layout.returnTo : "table")};
}
export function DocumentLayoutSwitch({mode, onChange}: {mode: DocumentLayoutMode; onChange: (mode: DocumentLayoutMode) => void}) {
  return <div className="document-layout-switch" role="radiogroup" aria-label="Отображение рабочей области">
    {modes.map((item, index) => <button key={item.mode} type="button" role="radio" aria-checked={mode === item.mode}
      tabIndex={mode === item.mode ? 0 : -1} onClick={() => onChange(item.mode)}
      onKeyDown={event => {
        const next = event.key === "ArrowRight" ? (index + 1) % 3 : event.key === "ArrowLeft" ? (index + 2) % 3 : event.key === "Home" ? 0 : event.key === "End" ? 2 : -1;
        if (next < 0) return;
        event.preventDefault(); onChange(modes[next].mode);
        event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("button")[next].focus();
      }}>{item.label}</button>)}
  </div>;
}
type Position = {top: number; left: number};
export function DocumentWorkspace({mode, onChange, list, detail, count, selectedLabel}: {
  mode: DocumentLayoutMode; onChange: (mode: DocumentLayoutMode) => void; list: ReactNode; detail: ReactNode; count: ReactNode; selectedLabel: string;
}) {
  const listScroll = useRef<HTMLDivElement>(null);
  const detailScroll = useRef<HTMLDivElement>(null);
  const positions = useRef<Partial<Record<DocumentLayoutMode, {list?: Position; detail?: Position}>>>({});
  const last = useRef<{list?: Position; detail?: Position}>({});
  const focus = useRef<"list" | "detail" | null>(null);
  function remember(panel: "list" | "detail", node: HTMLDivElement) {
    if ((panel === "list" && mode === "card") || (panel === "detail" && mode === "table")) return;
    const position = {top: node.scrollTop, left: node.scrollLeft};
    positions.current[mode] = {...positions.current[mode], [panel]: position};
    last.current[panel] = position;
  }
  useLayoutEffect(() => {
    for (const panel of ["list", "detail"] as const) {
      if ((panel === "list" && mode === "card") || (panel === "detail" && mode === "table")) continue;
      const node = panel === "list" ? listScroll.current : detailScroll.current;
      const position = positions.current[mode]?.[panel] || last.current[panel];
      if (node && position) {node.scrollTop = position.top; node.scrollLeft = position.left;}
    }
    if (focus.current) {
      (focus.current === "list" ? listScroll.current : detailScroll.current)?.focus({preventScroll: true});
      focus.current = null;
    }
  }, [mode]);
  function restore(panel: "list" | "detail") {focus.current = panel; onChange("split");}
  return <div className="document-workspace" data-layout={mode}>
    <section className="document-list-pane" aria-label="Результаты поиска">
      <button type="button" className="document-panel-rail list-rail" hidden={mode !== "card"}
        aria-label="Показать список документов" onClick={() => restore("list")}><span aria-hidden="true">›</span><span>Документы</span></button>
      <div className="document-list-body" hidden={mode === "card"}>
        <div ref={listScroll} className="document-pane-scroll" role="region" aria-label="Список документов" tabIndex={0}
          onScroll={event => remember("list", event.currentTarget)}>{count}{list}</div>
      </div>
    </section>
    <aside className="document-detail-pane" aria-label="Карточка выбранного документа">
      <button type="button" className="document-panel-rail detail-rail" hidden={mode !== "table"}
        aria-label="Показать карточку документа" onClick={() => restore("detail")}><span aria-hidden="true">‹</span><span>{selectedLabel || "Карточка"}</span></button>
      <div ref={detailScroll} className="document-detail-body document-pane-scroll" hidden={mode === "table"}
        role="region" aria-label="Содержимое карточки" tabIndex={0} onScroll={event => remember("detail", event.currentTarget)}>{detail}</div>
    </aside>
  </div>;
}
