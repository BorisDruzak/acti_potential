import type { FileVersion } from "../engine/schema";
import { FileActions } from "./OfficeSync";

export function OriginalActions({files, canDownload, mode, csrfToken}: {
  files: FileVersion[]; canDownload: boolean; mode: "editor" | "viewer"; csrfToken: string;
}) {
  if (!files.length) return null;
  return <div className="original-actions">
    {files.map(file => <div className="file-line" key={file.id}>
      <span><strong>{file.name}</strong></span>
      {canDownload && <FileActions file={file} mode={mode} csrfToken={csrfToken} compact/>}
    </div>)}
  </div>;
}
