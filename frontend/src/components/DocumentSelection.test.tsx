import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DocumentResults } from "./DocumentResults";
import { useDocumentSelection } from "../engine/useDocumentSelection";
import { resultColumns } from "../engine/documentPresentation";
import type { Card, FormSchema } from "../engine/schema";

afterEach(cleanup);
it("keeps checkbox, ranges and context selection independent from the open card", () => {
  const {result} = renderHook(() => useDocumentSelection(["a", "b", "c", "d"]));
  act(() => result.current.select("a", {toggle: true}));
  act(() => result.current.select("c", {range: true}));
  expect([...result.current.ids]).toEqual(["a", "b", "c"]);
  act(() => result.current.context("b"));
  expect([...result.current.ids]).toEqual(["a", "b", "c"]);
  act(() => result.current.context("d"));
  expect([...result.current.ids]).toEqual(["d"]);
});
it("retains configured nonstandard columns and their order plus separate legal act status", () => {
  const schema: FormSchema = {sections: [{key: "s", title: "S", fields: [{key: "custom", label: "Срок", type: "date"}]}], list_view: {columns: [{key: "custom", label: "Дата исполнения"}]}};
  const columns = resultColumns([schema]);
  expect(columns.map(column => column.key)).toContain("custom");
  expect(columns.find(column => column.key === "status")?.label).toBe("Статус правового акта");
  expect(columns.find(column => column.key === "card_state")?.label).toBe("Состояние карточки");
  const onSelect = vi.fn(); const selection = vi.fn(); const menu = vi.fn();
  const item = {id: "a", title: "Акт", schema_version_id: "v", values: {custom: "2026-10-17"}} as unknown as Card;
  render(<DocumentResults results={[{document: item, matches: []}]} schemas={new Map([["v", schema]])} sortSchemas={[schema]} types={[]} roles={["editor"]} readOnly={false} selected="a" selectedIds={new Set(["a"])} query="" sort="auto" direction="asc" typeId="" onSort={vi.fn()} onSelect={onSelect} onOpen={vi.fn()} onSelection={selection} onContextMenu={menu}/>);
  fireEvent.click(screen.getByRole("checkbox", {name: "Выделить документ «Акт»"}));
  expect(selection).toHaveBeenCalled(); expect(onSelect).not.toHaveBeenCalled();
  fireEvent.contextMenu(screen.getByRole("row", {name: /Выбрать документ «Акт»/}));
  expect(menu).toHaveBeenCalled(); expect(onSelect).not.toHaveBeenCalled();
  expect(screen.getByRole("gridcell", {name: "Дата исполнения: 2026-10-17"})).toBeTruthy();
});

it("caps range and select-all at the authenticated export bound", () => {
  const rows = Array.from({length: 1100}, (_, index) => String(index));
  const {result} = renderHook(() => useDocumentSelection(rows));
  act(() => result.current.all()); expect(result.current.ids.size).toBe(1000);
  act(() => result.current.select("1050", {toggle: true})); expect(result.current.ids.size).toBe(1000);
  act(() => result.current.clear()); act(() => result.current.select("0"));
  act(() => result.current.select("1099", {range: true})); expect(result.current.ids.size).toBe(1000);
});
