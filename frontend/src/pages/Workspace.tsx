import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Layout } from "../components/Layout";
import { SectionNotice } from "../components/SectionNotice";

function PlatformStatus() {
  const [data, setData] = useState<{ version: string; worker: string } | null>(
    null,
  );
  const [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    apiRequest<{ version: string; worker: string }>("/admin/platform", {
      signal: abort.signal,
    })
      .then(setData)
      .catch((e) => {
        if (!abort.signal.aborted) setError(e.message);
      });
    return () => abort.abort();
  }, []);
  return (
    <>
      <div className="page-heading">
        <span className="eyebrow">УПРАВЛЕНИЕ ПЛАТФОРМОЙ</span>
        <h1>Администрирование</h1>
        <p>Текущее состояние платформенного ядра.</p>
      </div>
      <section className="panel">
        <h2>Состояние сервисов</h2>
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : !data ? (
          <p role="status">Загрузка состояния…</p>
        ) : (
          <dl>
            <div>
              <dt>Версия платформы</dt>
              <dd>{data.version}</dd>
            </div>
            <div>
              <dt>Фоновый обработчик</dt>
              <dd>{data.worker === "available" ? "Работает" : "Недоступен"}</dd>
            </div>
            <div>
              <dt>Способ входа</dt>
              <dd>Локальная учётная запись</dd>
            </div>
          </dl>
        )}
        <p className="muted">
          Управление пользователями и настройками появится на следующих этапах.
        </p>
      </section>
    </>
  );
}

export function Workspace() {
  const auth = useAuth();
  const [section, setSection] = useState("Моя работа");
  let content;
  if (section === "Моя работа")
    content = (
      <>
        <div className="page-heading">
          <span className="eyebrow">МОЯ РАБОТА</span>
          <h1>Рабочее пространство</h1>
          <p>Добро пожаловать, {auth.user?.display_name}.</p>
        </div>
        <section className="welcome-panel">
          <span className="badge green">Платформенное ядро</span>
          <h2>Основа для совместной работы</h2>
          <p>
            Вход выполнен. Ваше рабочее пространство готово к подключению
            модулей документов и процессов.
          </p>
          <div className="session-note">
            <i /> Защищённая серверная сессия
          </div>
        </section>
        <div className="section-title">
          <h2>Разделы системы</h2>
          <span className="muted">Следующие этапы развития</span>
        </div>
        <div className="module-grid">
          {[
            ["Документы", "Карточки, версии и оригиналы документов."],
            ["Процессы", "Последовательности действий и их основания."],
            ["Поиск", "Поиск по реквизитам и содержанию."],
          ].map(([title, text]) => (
            <button
              className="module-card"
              key={title}
              onClick={() => setSection(title)}
            >
              <span className="badge">В разработке</span>
              <h3>
                {title}
                <span aria-hidden="true">↗</span>
              </h3>
              <p>{text}</p>
            </button>
          ))}
        </div>
        <p className="foundation-note">
          На этом этапе доступны вход, выход и базовая навигация. Документы и
          рабочие процессы будут подключаться последовательно.
        </p>
      </>
    );
  else if (
    section === "Администрирование" &&
    auth.user?.roles.includes("admin")
  )
    content = <PlatformStatus />;
  else content = <SectionNotice title={section} />;
  return (
    <Layout section={section} setSection={setSection}>
      {content}
    </Layout>
  );
}
