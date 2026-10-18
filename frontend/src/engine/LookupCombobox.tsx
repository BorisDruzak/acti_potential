import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export type LookupChoice = { id: string; label: string; name?: string; active?: boolean };

type Props = {
  id: string; label: string; choices: LookupChoice[]; selected: LookupChoice[];
  multiple: boolean; query: string; onQuery: (query: string) => void;
  onSelect: (id: string) => void; onRemove: (id: string) => void;
  disabled: boolean; busy: boolean; atLimit: boolean; required: boolean;
  validation: string; placeholder: string; children?: ReactNode;
};

export function LookupCombobox({ id, label, choices, selected, multiple, query, onQuery, onSelect, onRemove,
  disabled, busy, atLimit, required, validation, placeholder, children }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<string>();
  const [position, setPosition] = useState({ left: 0, top: 0, width: 0, maxHeight: 240 });
  const expanded = open && !disabled;
  const available = choices.filter(choice => choice.active !== false && !selected.some(item => item.id === choice.id) && !atLimit && !busy);
  const activeChoice = available.find(choice => choice.id === active);
  useEffect(() => { input.current?.setCustomValidity(validation); }, [validation]);
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: Event) => {
      const target = event.target;
      if (target instanceof Node && !input.current?.contains(target) && !panel.current?.contains(target)) {
        setOpen(false); setActive(undefined);
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("focusin", outside); };
  }, [expanded]);
  useLayoutEffect(() => {
    if (!expanded) return;
    const update = () => {
      const rect = input.current?.getBoundingClientRect();
      if (!rect) return;
      const below = window.innerHeight - rect.bottom - 12;
      const above = rect.top - 12;
      const upward = below < 160 && above > below;
      const maxHeight = Math.max(80, Math.min(280, upward ? above : below));
      setPosition({ left: Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8)),
        top: upward ? rect.top - 4 : rect.bottom + 4, width: Math.min(rect.width, window.innerWidth - 16), maxHeight });
      if (panel.current) panel.current.style.transform = upward ? "translateY(-100%)" : "";
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    const observer = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(update);
    if (input.current) observer?.observe(input.current);
    return () => { window.removeEventListener("resize", update); window.removeEventListener("scroll", update, true); observer?.disconnect(); };
  }, [expanded]);
  useEffect(() => {
    if (!activeChoice) return;
    document.getElementById(`${id}-option-${activeChoice.id}`)?.scrollIntoView?.({ block: "nearest" });
  }, [activeChoice?.id, id]);
  const choose = (choice: LookupChoice) => {
    if (busy || disabled || atLimit || choice.active === false || selected.some(item => item.id === choice.id)) return;
    onSelect(choice.id);
    setActive(undefined);
    if (!multiple) setOpen(false);
    input.current?.focus();
  };
  return <div className="lookup-combobox">
    {multiple && selected.length > 0 && <div className="lookup-chips" aria-label={`Выбрано: ${label}`}>
      {selected.map(choice => <span className="lookup-chip" key={choice.id}>
        <span>{choice.label}{choice.active === false ? " (неактивно)" : ""}</span>
        <button type="button" aria-label={`Убрать ${choice.label}`} disabled={disabled} onClick={() => onRemove(choice.id)}>×</button>
      </span>)}
    </div>}
    <div className="lookup-input-row">
      <input ref={input} id={id} role="combobox" type="text" autoComplete="off"
        aria-label={label} aria-autocomplete="list" aria-expanded={expanded} aria-controls={expanded ? `${id}-list` : undefined}
        aria-activedescendant={expanded && activeChoice ? `${id}-option-${activeChoice.id}` : undefined}
        aria-required={required || undefined} disabled={disabled} placeholder={placeholder}
        value={!multiple && !expanded ? selected[0]?.label || query : query}
        onFocus={() => setOpen(true)}
        onBlur={event => { if (!panel.current?.contains(event.relatedTarget)) { setOpen(false); onQuery(""); setActive(undefined); } }}
        onChange={event => { setOpen(true); setActive(undefined); onQuery(event.target.value); }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); setOpen(true);
            const index = available.findIndex(choice => choice.id === active);
            const next = event.key === "ArrowDown" ? (index + 1) % available.length : (index <= 0 ? available.length - 1 : index - 1);
            setActive(available[next]?.id);
          } else if (event.key === "Enter" && expanded) {
            event.preventDefault(); if (activeChoice) choose(activeChoice);
          } else if (event.key === "Escape") {
            event.preventDefault(); setOpen(false); onQuery(""); setActive(undefined);
          } else if (event.key === "Backspace" && multiple && !query && selected.length) {
            onRemove(selected[selected.length - 1].id);
          }
        }} />
      {!multiple && selected.length > 0 && <button type="button" className="lookup-clear" disabled={disabled}
        aria-label={`Убрать ${selected[0].label}`} onClick={() => onRemove(selected[0].id)}>×</button>}
    </div>
    {expanded && createPortal(<div className="lookup-popup" ref={panel} style={position}
      onMouseDown={event => { if (!(event.target instanceof HTMLElement) || !event.target.closest("button")) event.preventDefault(); }}>
      <div role="listbox" id={`${id}-list`} aria-label={label} aria-multiselectable={multiple || undefined} aria-busy={busy}>
        {choices.map(choice => {
          const isSelected = selected.some(item => item.id === choice.id);
          const unavailable = busy || choice.active === false || atLimit && !isSelected;
          return <div role="option" id={`${id}-option-${choice.id}`} key={choice.id}
            aria-selected={isSelected} aria-disabled={unavailable} title={choice.label}
            className={`lookup-option${activeChoice?.id === choice.id ? " lookup-option-focused" : ""}`}
            onMouseMove={() => { if (!unavailable && !isSelected) setActive(choice.id); }} onClick={() => choose(choice)}>
            {choice.label}{choice.active === false ? " (неактивно)" : ""}{isSelected ? " ✓" : ""}
          </div>;
        })}
      </div>
      {children}
    </div>, document.body)}
  </div>;
}
