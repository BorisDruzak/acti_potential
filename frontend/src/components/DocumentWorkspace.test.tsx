import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DocumentLayoutSwitch, DocumentWorkspace, useDocumentLayout } from "./DocumentWorkspace";

afterEach(() => {cleanup(); localStorage.clear(); vi.unstubAllGlobals();});
function Workspace({owner = "resize-user"}: {owner?: string}) {
  const {layout, choose, resize, restore} = useDocumentLayout(owner, false);
  return <><DocumentLayoutSwitch mode={layout.mode} onChange={choose}/>
    <DocumentWorkspace layout={layout} onChange={choose} onResize={resize} onRestore={restore}
      count="2 документа" selectedLabel="Альфа" list={<p>Альфа · Р-1 · 2026-10-16</p>}
      detail={<input aria-label="Черновик" defaultValue="Исходный текст"/>}/></>;
}
const separator = () => screen.getByRole("separator", {name: "Размер списка документов и карточки"});
const checked = (name: string) => screen.getByRole("radio", {name}).getAttribute("aria-checked");

function pointerEvents() {
  class Pointer extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit) {super(type, init); this.pointerId = init.pointerId || 1;}
  }
  vi.stubGlobal("PointerEvent", Pointer);
}

it("resizes from the keyboard, snaps to both full modes and reopens panes without losing the draft", () => {
  render(<Workspace/>);
  const input = screen.getByLabelText("Черновик");
  fireEvent.change(input, {target: {value: "Не потерять"}});
  expect(separator().getAttribute("aria-valuenow")).toBe("35");
  fireEvent.keyDown(separator(), {key: "ArrowRight"});
  expect(Number(separator().getAttribute("aria-valuenow"))).toBeGreaterThan(35);
  fireEvent.keyDown(separator(), {key: "End"});
  expect(checked("Таблица")).toBe("true");
  fireEvent.keyDown(separator(), {key: "ArrowLeft"});
  expect(checked("Вместе")).toBe("true");
  fireEvent.keyDown(separator(), {key: "Home"});
  expect(checked("Карточка")).toBe("true");
  fireEvent.keyDown(separator(), {key: "ArrowRight"});
  expect(checked("Вместе")).toBe("true");
  expect(screen.getByLabelText("Черновик")).toBe(input);
  expect((input as HTMLInputElement).value).toBe("Не потерять");
});

it("captures a pointer across snapping edges and restores the original layout on Escape", () => {
  pointerEvents();
  const {container} = render(<Workspace/>);
  const workspace = container.querySelector(".document-workspace")!;
  vi.spyOn(workspace, "getBoundingClientRect").mockReturnValue({left: 0, top: 0, width: 1000, height: 700} as DOMRect);
  const list = screen.getByRole("region", {name: "Список документов"});
  const detail = screen.getByRole("region", {name: "Содержимое карточки"});
  list.scrollTop = 900; detail.scrollTop = 240;
  const handle = separator();
  Object.assign(handle, {setPointerCapture: vi.fn(), releasePointerCapture: vi.fn(), hasPointerCapture: () => true});
  fireEvent.pointerDown(handle, {pointerId: 1, button: 0, clientX: 350});
  fireEvent.pointerMove(handle, {pointerId: 1, clientX: 700});
  expect(Number(separator().getAttribute("aria-valuenow"))).toBeGreaterThan(65);
  fireEvent.pointerUp(handle, {pointerId: 2});
  fireEvent.pointerCancel(handle, {pointerId: 2});
  fireEvent.pointerMove(handle, {pointerId: 1, clientX: 990});
  expect(checked("Таблица")).toBe("true");
  fireEvent.pointerMove(handle, {pointerId: 1, clientX: 5});
  expect(checked("Карточка")).toBe("true");
  list.scrollTop = 0; detail.scrollTop = 0;
  fireEvent.scroll(list); fireEvent.scroll(detail);
  fireEvent.keyDown(handle, {key: "Escape"});
  expect(checked("Вместе")).toBe("true");
  expect(separator().getAttribute("aria-valuenow")).toBe("35");
  expect(list.scrollTop).toBe(900); expect(detail.scrollTop).toBe(240);
});

it("persists the split width per user, preserves it through full modes and reads old preferences", () => {
  localStorage.setItem("acti.documents.layout.v1.resize-user.editor", JSON.stringify({mode: "table", returnTo: "split"}));
  const view = render(<Workspace/>);
  expect(checked("Таблица")).toBe("true");
  fireEvent.click(screen.getByRole("radio", {name: "Вместе"}));
  expect(separator().getAttribute("aria-valuenow")).toBe("35");
  fireEvent.keyDown(separator(), {key: "ArrowRight", shiftKey: true});
  const ratio = separator().getAttribute("aria-valuenow");
  fireEvent.click(screen.getByRole("radio", {name: "Карточка"}));
  view.unmount();
  render(<Workspace/>);
  fireEvent.click(screen.getByRole("button", {name: "Показать список документов"}));
  expect(separator().getAttribute("aria-valuenow")).toBe(ratio);
  cleanup(); render(<Workspace owner="another-user"/>);
  expect(separator().getAttribute("aria-valuenow")).toBe("35");
});

it("keeps the final resized scroll position when switching away and back", () => {
  pointerEvents();
  const {container} = render(<Workspace/>);
  const workspace = container.querySelector(".document-workspace")!;
  vi.spyOn(workspace, "getBoundingClientRect").mockReturnValue({left: 0, top: 0, width: 1000, height: 700} as DOMRect);
  const list = screen.getByRole("region", {name: "Список документов"});
  list.scrollTop = 900; fireEvent.scroll(list);
  const handle = separator();
  Object.assign(handle, {setPointerCapture: vi.fn(), releasePointerCapture: vi.fn(), hasPointerCapture: () => true});
  fireEvent.pointerDown(handle, {pointerId: 1, button: 0, clientX: 350});
  fireEvent.pointerMove(handle, {pointerId: 1, clientX: 700});
  list.scrollTop = 100; fireEvent.scroll(list);
  fireEvent.pointerUp(handle, {pointerId: 1});
  fireEvent.click(screen.getByRole("radio", {name: "Таблица"}));
  fireEvent.click(screen.getByRole("radio", {name: "Вместе"}));
  expect(list.scrollTop).toBe(100);
});
