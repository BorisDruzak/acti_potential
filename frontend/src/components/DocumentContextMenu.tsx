import { useEffect, useRef } from "react";
export function DocumentContextMenu({point, count, single, canDelete, canExport, busy, onClose, onOpen, onOriginal, onCopy, onExport, onDelete}: {
  point: {x: number; y: number}; count: number; single: boolean; canDelete: boolean; canExport: boolean; busy: boolean; onClose: () => void;
  onOpen: () => void; onOriginal: () => void; onCopy: () => void; onExport: (format: "csv" | "xlsx") => void; onDelete: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    root.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    const outside = (event: PointerEvent) => {if (!root.current?.contains(event.target as Node)) onClose();};
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [onClose]);
  function run(action: () => void) {action(); onClose();}
  return <div ref={root} role="menu" aria-label={`Действия с выбранными документами: ${count}`} className="document-context-menu" style={{left: Math.max(8, Math.min(point.x, window.innerWidth - 285)), top: Math.max(8, Math.min(point.y, window.innerHeight - 320))}}
    onKeyDown={event => {
      if (event.key === "Escape") {event.preventDefault(); onClose();}
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault(); const buttons = [...(root.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") || [])];
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        buttons[event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
      }
    }}>
    <span className="muted">Выбрано: {count}</span>
    <button role="menuitem" disabled={!single || busy} onClick={() => run(onOpen)}>Открыть карточку</button>
    <button role="menuitem" disabled={!single || busy} onClick={() => run(onOriginal)}>Открыть оригинал</button>
    <button role="menuitem" disabled={busy} onClick={() => run(onCopy)}>Копировать номер и название</button>
    <button role="menuitem" disabled={busy || !canExport} onClick={() => run(() => onExport("csv"))}>Экспорт выбранных CSV</button>
    <button role="menuitem" disabled={busy || !canExport} onClick={() => run(() => onExport("xlsx"))}>Экспорт выбранных XLSX</button>
    {canDelete && <button role="menuitem" disabled={busy} className="danger" onClick={() => run(onDelete)}>Удалить выбранные документы</button>}
    <button role="menuitem" onClick={onClose}>Закрыть меню</button>
  </div>;
}
