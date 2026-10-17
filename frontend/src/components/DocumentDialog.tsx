import { useEffect, useRef, type ReactNode } from "react";
const focusable = 'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]';
export function DocumentDialog({children, label, labelledBy, onClose}: {children: ReactNode; label?: string; labelledBy?: string; onClose: () => void}) {
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    root.current?.querySelector<HTMLElement>('[autofocus]')?.focus();
    if (!root.current?.contains(document.activeElement)) root.current?.querySelector<HTMLElement>(focusable)?.focus();
    return () => {if (previous?.isConnected) previous.focus({preventScroll: true});};
  }, []);
  return <div className="document-modal-backdrop"><section ref={root} className="panel document-dialog" role="dialog" aria-modal="true" aria-label={label} aria-labelledby={labelledBy}
    onKeyDown={event => {
      if (event.key === "Escape") {event.preventDefault(); onClose();}
      if (event.key !== "Tab") return;
      const nodes = [...(root.current?.querySelectorAll<HTMLElement>(focusable) || [])];
      const first = nodes[0]; const last = nodes.at(-1);
      if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
    }}>{children}</section></div>;
}
