import { useCallback, useEffect, useRef, useState } from "react";
export const MAX_DOCUMENT_SELECTION = 1000;
export type SelectionGesture = {toggle?: boolean; range?: boolean};
/** Selection is deliberately independent of the card currently open in the workspace. */
export function useDocumentSelection(order: string[]) {
  const [ids, setIds] = useState<Set<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const orderKey = JSON.stringify(order);
  useEffect(() => {const available = new Set<string>(JSON.parse(orderKey)); setIds(current => {
    const next = new Set([...current].filter(id => available.has(id)));
    return next.size === current.size ? current : next;
  });}, [orderKey]);
  const select = useCallback((id: string, gesture: SelectionGesture = {}) => {
    const rows: string[] = JSON.parse(orderKey);
    setIds(current => {
      if (gesture.range && anchor.current && rows.includes(anchor.current)) {
        const from = rows.indexOf(anchor.current); const to = rows.indexOf(id);
        return new Set([...(gesture.toggle ? current : []), ...rows.slice(Math.min(from, to), Math.max(from, to) + 1)].slice(0, MAX_DOCUMENT_SELECTION));
      }
      anchor.current = id;
      if (!gesture.toggle) return new Set([id]);
      const next = new Set(current); if (next.has(id)) next.delete(id); else if (next.size < MAX_DOCUMENT_SELECTION) next.add(id); return next;
    });
  }, [orderKey]);
  const context = useCallback((id: string) => {setIds(current => current.has(id) ? current : new Set([id])); anchor.current = id;}, []);
  const clear = useCallback(() => setIds(new Set()), []);
  const all = useCallback(() => setIds(new Set<string>((JSON.parse(orderKey) as string[]).slice(0, MAX_DOCUMENT_SELECTION))), [orderKey]);
  return {ids, select, context, clear, all};
}
