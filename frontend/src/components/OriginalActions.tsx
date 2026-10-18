import { useState } from "react";
import type { FileVersion } from "../engine/schema";
import { saveDownload } from "../api/client";
import { useAction } from "../engine/useData";
import { Feedback } from "./Feedback";
import { DocumentDialog } from "./DocumentDialog";
import { OfficeOpen } from "./OfficeOpen";

export function OriginalActions({files, canDownload, mode, csrfToken}: {
  files: FileVersion[]; canDownload: boolean; mode: "editor" | "viewer"; csrfToken: string;
}) {
  const [choosing, setChoosing] = useState(false);
  const action = useAction();
  if (!canDownload || !files.length) return null;
  function actions(file: FileVersion) {
    return <><OfficeOpen file={file} mode={mode} csrfToken={csrfToken}/><button type="button" disabled={action.busy}
      onClick={() => void action.run(() => saveDownload(file.id, file.name, mode), "Оригинал скачан")}>Скачать</button></>;
  }
  return <div className="original-actions">
    {files.length === 1 ? actions(files[0]) : <button type="button" className="primary" onClick={() => setChoosing(true)}>Открыть оригинал</button>}
    {!choosing && <Feedback error={action.error} message={action.message}/>}
    {choosing && <DocumentDialog label="Выбрать оригинал" onClose={() => setChoosing(false)}>
      <div className="toolbar"><h2>Выбрать оригинал</h2><button onClick={() => setChoosing(false)}>Закрыть</button></div>
      <p>Выберите файл и версию оригинала.</p>
      {files.map(file => <div className="file-line" key={file.id}><span><strong>{file.name}</strong><small>Версия {file.version}</small></span>{actions(file)}</div>)}
      <Feedback error={action.error}/>
    </DocumentDialog>}
  </div>;
}
