import {act, cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {RegistrationNumberControl} from "./RegistrationNumberControl";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
const base = {id: "reg-number", registryId: "registry", documentTypeId: "kind", schemaVersionId: "schema", csrfToken: "csrf", value: "", onChange: vi.fn(), onReserved: vi.fn()};
it("reserves for the selected registration date and legal act type", async () => {
 const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({number:"1",number_entry_id:"entry",series_id:"series"})));vi.stubGlobal("fetch",fetch);
 const reserved=vi.fn();
 render(<RegistrationNumberControl {...base} registrationDate="2025-12-15" registrationTypeId="resolution" onReserved={reserved}/>);
 fireEvent.click(screen.getByRole("button",{name:"Присвоить номер"}));
 await waitFor(()=>expect(reserved).toHaveBeenCalled());
 expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({reg_date:"2025-12-15",type_id:"resolution"});
});
it("reserves an explicit number next to the editable field and sends exact document revision", async () => {
 const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({number: "42", number_entry_id: "entry", series_id: "series"}), {status: 200}));vi.stubGlobal("fetch",fetch);
 const reserved=vi.fn(),busy=vi.fn();
 render(<RegistrationNumberControl {...base} documentId="document" revision={4} onReserved={reserved} onBusyChange={busy}/>);
 fireEvent.click(screen.getByRole("button",{name:"Присвоить номер"}));
 await waitFor(()=>expect(reserved).toHaveBeenCalledWith({number:"42",number_entry_id:"entry",series_id:"series"}));
 expect(fetch.mock.calls[0][0]).toBe("/api/v1/registries/registry/next-number");
 const body=JSON.parse(fetch.mock.calls[0][1].body);
 expect(body).toMatchObject({document_type_id:"kind",schema_version_id:"schema",document_id:"document",revision:4});
 expect(body.idempotency_key).toBeTruthy();expect(busy.mock.calls).toEqual([[true],[false]]);
});
it("accepts arbitrary manual number text only through an explicit editable mode and preserves assigned locks", () => {
 const changed=vi.fn();const {rerender}=render(<RegistrationNumberControl {...base} value="42" onChange={changed}/>);
 fireEvent.click(screen.getByRole("button",{name:"Ввести произвольный"}));
 fireEvent.change(screen.getByRole("textbox",{name:"Регистрационный номер"}),{target:{value:"MANUAL/45"}});
 expect(changed).toHaveBeenCalledWith("MANUAL/45");
 rerender(<RegistrationNumberControl {...base} value="42" locked/>);
 expect((screen.getByRole("textbox",{name:"Регистрационный номер"})as HTMLInputElement).readOnly).toBe(true);
 expect((screen.getByRole("button",{name:"Присвоить номер"})as HTMLButtonElement).disabled).toBe(true);
});
it("does not apply a late reservation after the control is removed", async () => {
 let answer!:(r:Response)=>void;vi.stubGlobal("fetch",vi.fn(()=>new Promise<Response>(r=>{answer=r;})));
 const reserved=vi.fn();const {unmount}=render(<RegistrationNumberControl {...base} onReserved={reserved}/>);
 fireEvent.click(screen.getByRole("button",{name:"Присвоить номер"}));unmount();
 await act(async()=>answer(new Response(JSON.stringify({number:"43",number_entry_id:"entry",series_id:"series"}))));
 expect(reserved).not.toHaveBeenCalled();
});
