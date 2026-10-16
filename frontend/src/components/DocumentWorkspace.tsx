import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent, type ReactNode } from "react";
import "./document-workspace.css";

export type DocumentLayoutMode = "table" | "split" | "card";
export type DocumentLayout = {mode: DocumentLayoutMode; returnTo: Exclude<DocumentLayoutMode, "card">; ratio: number};
const modes = [{mode: "table", label: "Таблица"}, {mode: "split", label: "Вместе"}, {mode: "card", label: "Карточка"}] as const;
const collapseRatio = .08;
function preference(key: string): DocumentLayout {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    if (modes.some(item => item.mode === value?.mode)) return {
      mode: value.mode, returnTo: value.returnTo === "table" ? "table" : "split",
      ratio: typeof value.ratio === "number" && Number.isFinite(value.ratio) && value.ratio > collapseRatio && value.ratio < 1 - collapseRatio ? value.ratio : .35,
    };
  } catch { /* Storage is optional. */ }
  return {mode: "split", returnTo: "split", ratio: .35};
}
export function useDocumentLayout(owner: string, readOnly: boolean) {
  const key = `acti.documents.layout.v1.${owner}.${readOnly ? "viewer" : "editor"}`;
  const [state, setState] = useState(() => ({key, ...preference(key)}));
  const layout = state.key === key ? state : {key, ...preference(key)};
  useEffect(() => {
    if (state.key !== key) {setState({key, ...preference(key)}); return;}
    try {localStorage.setItem(key, JSON.stringify({mode: state.mode, returnTo: state.returnTo, ratio: state.ratio}));} catch { /* Keep the current layout in memory. */ }
  }, [state, key]);
  function choose(mode: DocumentLayoutMode) {
    setState(current => {
      const previous = current.key === key ? current : {key, ...preference(key)};
      return {...previous, mode, returnTo: mode === "card" ? previous.mode === "card" ? previous.returnTo : previous.mode : mode};
    });
  }
  function resize(ratio: number) {
    if (!Number.isFinite(ratio)) return;
    setState(current => {
      const previous = current.key === key ? current : {key, ...preference(key)};
      const mode = ratio <= collapseRatio ? "card" : ratio >= 1 - collapseRatio ? "table" : "split";
      return {...previous, mode, ratio: mode === "split" ? ratio : previous.ratio,
        returnTo: mode === "card" ? previous.mode === "card" ? previous.returnTo : previous.mode : mode};
    });
  }
  function restore(value: DocumentLayout) {setState({key, ...value});}
  return {layout, choose, resize, restore, back: () => choose(layout.mode === "card" ? layout.returnTo : "table")};
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
type PanePositions = {list?: Position; detail?: Position};
type ReadingAnchor = {id: string; offset: number};
function readingAnchor(node: HTMLDivElement | null): ReadingAnchor | undefined {
  if (!node || !node.scrollTop) return;
  const top = node.getBoundingClientRect().top + (node.querySelector("thead")?.getBoundingClientRect().height || 0);
  const row = Array.from(node.querySelectorAll<HTMLTableRowElement>("tbody tr[data-document-id]")).find(row => row.getBoundingClientRect().bottom > top);
  return row ? {id: row.dataset.documentId!, offset: row.getBoundingClientRect().top - top} : undefined;
}
function restoreAnchor(node: HTMLDivElement | null, anchor?: ReadingAnchor) {
  if (!node || !anchor) return;
  const row = Array.from(node.querySelectorAll<HTMLTableRowElement>("tbody tr[data-document-id]")).find(row => row.dataset.documentId === anchor.id);
  if (row) node.scrollTop += row.getBoundingClientRect().top - node.getBoundingClientRect().top - (node.querySelector("thead")?.getBoundingClientRect().height || 0) - anchor.offset;
}
export function DocumentWorkspace({layout, onChange, onResize, onRestore, list, detail, count, selectedLabel, busy = false}: {
  layout: DocumentLayout; onChange: (mode: DocumentLayoutMode) => void; onResize: (ratio: number) => void; onRestore: (layout: DocumentLayout) => void;
  list: ReactNode; detail: ReactNode; count: ReactNode; selectedLabel: string; busy?: boolean;
}) {
  const {mode, ratio} = layout;
  const workspace = useRef<HTMLDivElement>(null);
  const handle = useRef<HTMLDivElement>(null);
  const listScroll = useRef<HTMLDivElement>(null);
  const detailScroll = useRef<HTMLDivElement>(null);
  const paneId = useId();
  const [stacked, setStacked] = useState(() => window.matchMedia?.("(max-width: 650px)").matches || false);
  const [resizing, setResizing] = useState(false);
  const [resetScroll, setResetScroll] = useState<PanePositions | null>(null);
  const keyboardAnchor = useRef<ReadingAnchor | undefined>(undefined);
  const drag = useRef<{pointerId: number; layout: DocumentLayout; start: number; size: number; handleSize: number; stacked: boolean; scroll: PanePositions; anchor?: ReadingAnchor} | null>(null);
  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 650px)");
    if (!media) return;
    const update = () => setStacked(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  function startResize(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || drag.current) return;
    const bounds = workspace.current!.getBoundingClientRect();
    const handleBounds = event.currentTarget.getBoundingClientRect();
    const scroll: PanePositions = {};
    if (mode !== "card") scroll.list = {top: listScroll.current!.scrollTop, left: listScroll.current!.scrollLeft};
    if (mode !== "table") scroll.detail = {top: detailScroll.current!.scrollTop, left: detailScroll.current!.scrollLeft};
    drag.current = {pointerId: event.pointerId, layout, stacked, start: stacked ? bounds.top : bounds.left,
      size: stacked ? bounds.height : bounds.width, handleSize: stacked ? handleBounds.height : handleBounds.width,
      scroll, anchor: mode === "card" ? undefined : readingAnchor(listScroll.current)};
    event.preventDefault(); event.currentTarget.focus({preventScroll: true});
    event.currentTarget.setPointerCapture(event.pointerId); setResizing(true);
  }
  function moveResize(event: PointerEvent<HTMLDivElement>) {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    event.preventDefault();
    const available = active.size - active.handleSize;
    if (available > 0) onResize(((active.stacked ? event.clientY : event.clientX) - active.start - active.handleSize / 2) / available);
  }
  function finishResize(cancel = false) {
    const active = drag.current;
    if (!active) return;
    drag.current = null; setResizing(false);
    if (cancel) {
      positions.current[active.layout.mode] = {...positions.current[active.layout.mode], ...active.scroll};
      last.current = {...last.current, ...active.scroll};
      setResetScroll(active.scroll); onRestore(active.layout);
    } else {
      if (listScroll.current) remember("list", listScroll.current);
      if (detailScroll.current) remember("detail", detailScroll.current);
    }
    if (handle.current?.hasPointerCapture(active.pointerId)) handle.current.releasePointerCapture(active.pointerId);
  }
  const positions = useRef<Partial<Record<DocumentLayoutMode, PanePositions>>>({});
  const last = useRef<PanePositions>({});
  const focus = useRef<"list" | "detail" | null>(null);
  function remember(panel: "list" | "detail", node: HTMLDivElement) {
    if (drag.current || (panel === "list" && mode === "card") || (panel === "detail" && mode === "table")) return;
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
  useLayoutEffect(() => {
    if (resetScroll) {
      for (const panel of ["list", "detail"] as const) {
        const node = panel === "list" ? listScroll.current : detailScroll.current;
        const position = resetScroll[panel];
        if (node && position) {node.scrollTop = position.top; node.scrollLeft = position.left;}
      }
      setResetScroll(null);
      return;
    }
    if (mode !== "card") restoreAnchor(listScroll.current, drag.current?.anchor || keyboardAnchor.current);
    if (mode !== "table" && drag.current?.scroll.detail && detailScroll.current) detailScroll.current.scrollTop = drag.current.scroll.detail.top;
    keyboardAnchor.current = undefined;
  }, [mode, ratio, resetScroll]);
  function restore(panel: "list" | "detail") {focus.current = panel; onChange("split");}
  const percent = mode === "card" ? 0 : mode === "table" ? 100 : Math.round(ratio * 100);
  return <div ref={workspace} className="document-workspace" data-layout={mode} data-resizing={resizing || undefined}
    style={{"--document-list-size": `${ratio}fr`, "--document-detail-size": `${1 - ratio}fr`} as CSSProperties}>
    <section id={`${paneId}-list`} className="document-list-pane" aria-label="Результаты поиска" aria-busy={busy}>
      <button type="button" className="document-panel-rail list-rail" hidden={mode !== "card"}
        aria-label="Показать список документов" onClick={() => restore("list")}><span aria-hidden="true">›</span><span>Документы</span></button>
      <div className="document-list-body" hidden={mode === "card"}>
        <div ref={listScroll} className="document-pane-scroll" role="region" aria-label="Список документов" tabIndex={0}
          onScroll={event => remember("list", event.currentTarget)}>{count}{list}</div>
      </div>
    </section>
    <div ref={handle} className="document-resize-handle" role="separator" tabIndex={0}
      aria-label="Размер списка документов и карточки" aria-orientation={stacked ? "horizontal" : "vertical"}
      aria-controls={`${paneId}-list ${paneId}-detail`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}
      aria-valuetext={`Документы ${percent}%, карточка ${100 - percent}%`}
      title="Потяните границу для изменения размеров. Стрелки — изменить, Home / End — развернуть панель, Esc — отменить перетаскивание."
      onPointerDown={startResize} onPointerMove={moveResize}
      onPointerUp={event => {if (event.pointerId === drag.current?.pointerId) finishResize();}}
      onPointerCancel={event => {if (event.pointerId === drag.current?.pointerId) finishResize(true);}}
      onLostPointerCapture={event => {if (event.pointerId === drag.current?.pointerId) finishResize();}}
      onKeyDown={event => {
        if (event.key === "Escape") {event.preventDefault(); finishResize(true); return;}
        if (drag.current) return;
        const decrease = event.key === (stacked ? "ArrowUp" : "ArrowLeft");
        const increase = event.key === (stacked ? "ArrowDown" : "ArrowRight");
        if (!["Home", "End"].includes(event.key) && !decrease && !increase) return;
        event.preventDefault();
        keyboardAnchor.current = mode === "card" ? undefined : readingAnchor(listScroll.current);
        if (event.key === "Home") onResize(0);
        else if (event.key === "End") onResize(1);
        else onResize(mode === "card" ? increase ? .12 : 0 : mode === "table" ? decrease ? .88 : 1 : ratio + (increase ? 1 : -1) * (event.shiftKey ? .1 : .02));
      }}><span aria-hidden="true"/></div>
    <aside id={`${paneId}-detail`} className="document-detail-pane" aria-label="Карточка выбранного документа">
      <button type="button" className="document-panel-rail detail-rail" hidden={mode !== "table"}
        aria-label="Показать карточку документа" onClick={() => restore("detail")}><span aria-hidden="true">‹</span><span>{selectedLabel || "Карточка"}</span></button>
      <div ref={detailScroll} className="document-detail-body document-pane-scroll" hidden={mode === "table"}
        role="region" aria-label="Содержимое карточки" tabIndex={0} onScroll={event => remember("detail", event.currentTarget)}>{detail}</div>
    </aside>
  </div>;
}
