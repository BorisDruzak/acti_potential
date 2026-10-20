import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DocumentFeedEnd } from "./DocumentFeedEnd";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it("rechecks an intersecting feed end after a silent background refresh", () => {
  const load = vi.fn();
  vi.stubGlobal("IntersectionObserver", class {
    constructor(private callback: IntersectionObserverCallback) {}
    observe() {this.callback([{isIntersecting: true}] as IntersectionObserverEntry[], this as unknown as IntersectionObserver);}
    disconnect() {}
  });
  const view = render(<DocumentFeedEnd hasMore loading={false} refreshing error="" onLoad={load} enabled/>);
  expect(load).not.toHaveBeenCalled();
  expect(screen.queryByText("Загрузка документов…")).toBeNull();
  view.rerender(<DocumentFeedEnd hasMore loading={false} refreshing={false} error="" onLoad={load} enabled/>);
  expect(load).toHaveBeenCalledOnce();
});
