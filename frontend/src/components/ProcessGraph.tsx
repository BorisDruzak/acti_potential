export function ProcessGraph({steps, transitions, selected, start, onSelect}: {
  steps: {key: string; title: string; terminal?: boolean}[];
  transitions: {key: string; from: string; to: string; label?: string; otherwise?: boolean; condition?: unknown}[];
  selected: string; start: string; onSelect: (key: string) => void;
}) {
  const byKey = new Map(steps.map(step => [step.key, step]));
  return <section className="process-graph" aria-label="Интерактивная схема процесса">
    <h3>Схема процесса</h3>
    <div className="process-nodes">{steps.map((step, index) => <button type="button" className={`graph-node${selected === step.key ? " selected" : ""}`} aria-current={selected === step.key ? "step" : undefined} key={step.key} onClick={() => onSelect(step.key)}><small>Шаг {index + 1}{step.key === start ? " · начало" : ""}{step.terminal ? " · завершение" : ""}</small><strong>{step.title || "Без названия"}</strong></button>)}</div>
    <details open><summary>Переходы процесса — схема и список</summary>
      {transitions.map(edge => <div className="relation-graph-row" key={edge.key}>
        <button type="button" className="graph-node" onClick={() => onSelect(edge.from)}>{byKey.get(edge.from)?.title || "Выберите исходный шаг"}</button>
        <div className="graph-edge"><span>{edge.otherwise ? "Иначе" : edge.label || (edge.condition ? "По условию" : "Следующий шаг")}</span><svg viewBox="0 0 120 20" role="img" aria-label="Направление перехода"><path d="M0 10 H112 M104 3 L113 10 L104 17" fill="none" stroke="currentColor" strokeWidth="2"/></svg></div>
        <button type="button" className="graph-node" onClick={() => onSelect(edge.to)}>{byKey.get(edge.to)?.title || "Выберите следующий шаг"}</button>
      </div>)}
      {!transitions.length && <p className="muted">Переходы ещё не добавлены.</p>}
    </details>
  </section>;
}
