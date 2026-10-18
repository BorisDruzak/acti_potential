import {useEffect, useRef, useState} from "react";
import {apiRequest} from "../api/client";
import "./RegistrationNumberControl.css";

export type NumberReservation = {number: string; number_entry_id: string; series_id: string};
export type RegistrationNumberProps = {
 id: string; label?: string; registryId: string; documentTypeId: string; schemaVersionId: string;
 documentId?: string; revision?: number; csrfToken: string; value: unknown;
 registrationDate?: unknown; registrationTypeId?: unknown;
 required?: boolean; maxLength?: number; pattern?: string; locked?: boolean; disabled?: boolean;
 onChange: (value: string) => void; onReserved: (result: NumberReservation) => void;
 onBusyChange?: (value: boolean) => void;
};

/** Reserves a journal entry; saving the form attaches it without changing other draft values. */
export function RegistrationNumberControl(props: RegistrationNumberProps) {
 const [busy,setBusy]=useState(false),[error,setError]=useState("");
 const [manual,setManual]=useState(true);
 const input=useRef<HTMLInputElement>(null),pending=useRef<AbortController|null>(null);
 const retry=useRef<string|null>(null);
 const callbacks=useRef(props);callbacks.current=props;
 const context=JSON.stringify([props.registryId,props.documentTypeId,props.schemaVersionId,props.documentId,props.revision,props.registrationDate,props.registrationTypeId]);
 useEffect(()=>{
  retry.current=null;setBusy(false);setError("");setManual(true);
  return ()=>{if(pending.current){pending.current.abort();pending.current=null;callbacks.current.onBusyChange?.(false);}};
 },[context]);
 async function reserve(){
  if(pending.current||props.disabled||props.locked)return;
  const controller=new AbortController();pending.current=controller;
  setBusy(true);setError("");props.onBusyChange?.(true);
  retry.current??=crypto.randomUUID();
  try{
   const result=await apiRequest<NumberReservation>(`/registries/${props.registryId}/next-number`,{
    method:"POST",signal:controller.signal,headers:{"X-CSRF-Token":props.csrfToken},
    body:JSON.stringify({document_type_id:props.documentTypeId,schema_version_id:props.schemaVersionId,
     ...(props.registrationDate!==undefined?{reg_date:props.registrationDate||null}:{}),
     ...(props.registrationTypeId!==undefined?{type_id:props.registrationTypeId||null}:{}),
     ...(props.documentId?{document_id:props.documentId,revision:props.revision}:{}),idempotency_key:retry.current}),
   });
   if(controller.signal.aborted)return;
   if(typeof result.number!=="string"||!result.number_entry_id||!result.series_id)throw new Error("Ответ присвоения номера неполный. Повторите действие.");
   retry.current=null;setManual(false);callbacks.current.onReserved(result);
  }catch(failure){if(!controller.signal.aborted)setError(failure instanceof Error?failure.message:"Не удалось присвоить номер");}
  finally{if(pending.current===controller){pending.current=null;setBusy(false);callbacks.current.onBusyChange?.(false);}}
 }
 const label=props.label||"Регистрационный номер";
 return <div className="registration-number-control">
  <div className="registration-number-row">
   <input ref={input} id={props.id} aria-label={label} value={String(props.value??"")} required={props.required}
    maxLength={props.maxLength??255} pattern={props.pattern} readOnly={props.locked||!manual} disabled={busy||props.disabled}
    onChange={event=>{setError("");props.onChange(event.target.value);}}/>
   <div className="registration-number-actions">
    <button type="button" aria-label="Присвоить номер" title="Присвоить номер" disabled={busy||props.disabled||props.locked} onClick={()=>void reserve()}>Присвоить</button>
    <button type="button" aria-label="Ввести произвольный" title="Ввести произвольный номер" disabled={busy||props.disabled||props.locked} aria-pressed={manual&&!props.locked}
     onClick={()=>{setManual(true);input.current?.focus();input.current?.select();}}>Ввести вручную</button>
   </div>
  </div>
  {busy?<small role="status">Присвоение номера…</small>:<small>{props.locked?"Номер закреплён в реестре номеров.":"Уникальность проверяется при сохранении."}</small>}
  {error&&<small className="error" role="alert">{error}</small>}
 </div>;
}
