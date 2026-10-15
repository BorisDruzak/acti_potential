import { useEffect, useRef, useState } from "react";
import type { OneTimeToken } from "../engine/serviceAccounts";

export function OneTimeSecret({value, onClose}: {value: OneTimeToken; onClose: () => void}) {
  const input = useRef<HTMLInputElement>(null);
  const [shown, setShown] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    const previous = document.activeElement;
    input.current?.focus();
    const close = (event: KeyboardEvent) => {if (event.key === "Escape") onClose();};
    window.addEventListener("keydown", close);
    return () => {window.removeEventListener("keydown", close); if (previous instanceof HTMLElement && previous.isConnected) previous.focus();};
  }, [onClose]);
  return <div className="secret-backdrop"><section className="panel one-time-secret" role="dialog" aria-modal="true" aria-labelledby="one-time-token-title" onKeyDown={event => {
    if (event.key !== "Tab") return;
    const controls = event.currentTarget.querySelectorAll<HTMLInputElement | HTMLButtonElement>("input, button");
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
    else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
  }}>
    <h2 id="one-time-token-title">Токен создан</h2><p>Сохраните токен в защищённом хранилище. После закрытия получить его повторно нельзя.</p>
    <label>Одноразовый показ токена<input ref={input} type={shown ? "text" : "password"} readOnly value={value.token} autoComplete="off" spellCheck={false} onFocus={event => event.target.select()}/></label>
    <p>Действует до {new Date(value.expires_at).toLocaleString("ru-RU")}.</p>
    <div className="button-row"><button type="button" onClick={() => setShown(current => !current)}>{shown ? "Скрыть токен" : "Показать токен"}</button><button type="button" onClick={() => {if (!navigator.clipboard?.writeText) {input.current?.select(); setMessage("Выделите токен и скопируйте его вручную."); return;} void navigator.clipboard.writeText(value.token).then(() => setMessage("Токен скопирован"), () => setMessage("Не удалось скопировать автоматически. Выделите токен и скопируйте его вручную."));}}>Скопировать токен</button><button type="button" className="primary" onClick={onClose}>Закрыть и скрыть токен</button></div>
    {message && <p role="status">{message}</p>}
  </section></div>;
}
