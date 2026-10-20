import { useLayoutEffect, useRef, type ReactNode } from "react";

/** Both creation and editing reserve the actual height of their sticky action bar. */
export function DocumentDock({children}: {children: ReactNode}) {
  const dock = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = dock.current;
    if (!node) return;
    const measure = () => {
      const height = `${Math.ceil(node.getBoundingClientRect().height)}px`;
      if (node.parentElement?.style.getPropertyValue("--document-card-dock-height") !== height)
        node.parentElement?.style.setProperty("--document-card-dock-height", height);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(node);
    window.addEventListener("resize", measure);
    return () => {observer?.disconnect(); window.removeEventListener("resize", measure);};
  }, []);
  return <div className="document-card-dock" ref={dock}>{children}</div>;
}
