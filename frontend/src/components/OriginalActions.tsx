import type { FileVersion } from "../engine/schema";
import { FileActions } from "./OfficeSync";

export function OriginalActions({files, canDownload, mode, csrfToken, hideSingleName = false}: {
  files: FileVersion[]; canDownload: boolean; mode: "editor" | "viewer"; csrfToken: string; hideSingleName?: boolean;
}) {
  if (!files.length) return null;
  return <div className="original-actions">
    {files.map(file => <div className="file-line" key={file.id}>
      {(!hideSingleName || files.length > 1) && <span><strong>{file.name}</strong></span>}
      {canDownload && <FileActions file={file} mode={mode} csrfToken={csrfToken} compact/>}
    </div>)}
  </div>;
}
