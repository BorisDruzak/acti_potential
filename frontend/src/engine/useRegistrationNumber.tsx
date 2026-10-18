import {useEffect, useRef, useState} from "react";
import {RegistrationNumberControl, type NumberReservation} from "../components/RegistrationNumberControl";
import {canRead, canWrite, editableValues, type FormSchema, type Values} from "./schema";

export function useRegistrationNumber(options: {
 schema: FormSchema; values: Values; roles: string[]; mode: "create" | "edit";
 registryId: string; documentTypeId: string; schemaVersionId: string; documentId?: string;
 revision?: number; csrfToken: string; locked?: boolean; disabled?: boolean; onValues: (values: Values)=>void;
}) {
 const [reservation,setReservation]=useState<NumberReservation|null>(null);
 const [busy,setBusy]=useState(false),pending=useRef(false);
 const current=useRef(options);current.current=options;
 const fields=options.schema.sections.flatMap(section=>section.fields);
 const field=fields.find(field=>field.system==="reg_number");
 const available=field&&canRead(field,options.mode,options.roles,options.values,undefined,fields)&&canWrite({...field,number_series_id:null},options.mode,options.roles);
 const context=JSON.stringify([options.registryId,options.documentTypeId,options.schemaVersionId,options.documentId,options.revision]);
 useEffect(()=>{setReservation(null);},[context]);
 const selected=reservation&&field&&options.values[field.key]===reservation.number?reservation:null;
 const reset=()=>setReservation(null);
 const fieldContent=available?{[field.key]:(id:string)=><RegistrationNumberControl id={id} label={field.label}
  registryId={options.registryId} documentTypeId={options.documentTypeId} schemaVersionId={options.schemaVersionId}
  documentId={options.documentId} revision={options.revision} csrfToken={options.csrfToken}
  value={options.values[field.key]} required={field.required} locked={options.locked} disabled={options.disabled}
  maxLength={Number(field.constraints?.max_length)||255} pattern={typeof field.constraints?.pattern==="string"?field.constraints.pattern:undefined}
  onChange={value=>{setReservation(null);current.current.onValues({...current.current.values,[field.key]:value});}}
  onReserved={result=>{setReservation(result);current.current.onValues({...current.current.values,[field.key]:result.number});}}
  onBusyChange={value=>{pending.current=value;setBusy(value);}}/>}:undefined;
 return {fieldContent,busy,pending,reset,numberEntryId:selected?.number_entry_id,
  serialize(){
   const values=editableValues(options.schema,options.values,options.mode,options.roles);
   if(available&&field){
    if(options.locked)delete values[field.key];
    else if(Object.hasOwn(options.values,field.key))values[field.key]=options.values[field.key];
   }
   return values;
  },
 };
}
