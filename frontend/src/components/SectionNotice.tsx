export function SectionNotice({ title }: { title: string }) {
  return (
    <>
      <div className="page-heading">
        <span className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</span>
        <h1>{title}</h1>
      </div>
      <section className="notice-panel">
        <span className="notice-icon" aria-hidden="true">
          ▤
        </span>
        <span className="badge">В разработке</span>
        <h2>Этот раздел появится на следующем этапе</h2>
        <p>
          Сейчас доступно платформенное ядро. Функции раздела «{title}» пока не
          реализованы.
        </p>
        <p className="muted">
          Здесь будут доступны реальные данные и действия после подключения
          соответствующего модуля.
        </p>
      </section>
    </>
  );
}
