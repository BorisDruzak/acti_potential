import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
const focusable = 'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]';
export function DocumentDialog({children, label, labelledBy, className = "", onClose}: {children: ReactNode; label?: string; labelledBy?: string; className?: string; onClose: () => void}) {
  const root = useRef<HTMLElement>(null);
  const [previous] = useState(() => document.activeElement as HTMLElement | null);
  useEffect(() => {
    const bodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const siblings: {element: HTMLElement; inert: boolean}[] = [];
    let branch = root.current?.parentElement;
    while (branch && branch !== document.body) {
      for (const sibling of branch.parentElement?.children || []) {
        if (sibling !== branch && sibling instanceof HTMLElement) {
          siblings.push({element: sibling, inert: sibling.inert}); sibling.inert = true;
        }
      }
      branch = branch.parentElement;
    }
    root.current?.querySelector<HTMLElement>('[autofocus]')?.focus();
    if (!root.current?.contains(document.activeElement)) root.current?.querySelector<HTMLElement>(focusable)?.focus();
    return () => {
      for (const {element, inert} of siblings) element.inert = inert;
      document.body.style.overflow = bodyOverflow;
      if (previous?.isConnected && !previous.closest("[inert]")) previous.focus({preventScroll: true});
    };
  }, [previous]);
  return createPortal(<div className={`document-modal-backdrop ${className ? className + "-backdrop" : ""}`} onPointerDown={event => {event.stopPropagation(); if (event.target === event.currentTarget) onClose();}}><section ref={root} className={`panel document-dialog ${className}`} role="dialog" aria-modal="true" aria-label={label} aria-labelledby={labelledBy}
    onKeyDown={event => {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {event.preventDefault(); onClose();}
      if (event.key !== "Tab") return;
      const nodes = [...(root.current?.querySelectorAll<HTMLElement>(focusable) || [])];
      const first = nodes[0]; const last = nodes.at(-1);
      if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
    }}>{children}</section></div>, document.body);
}
