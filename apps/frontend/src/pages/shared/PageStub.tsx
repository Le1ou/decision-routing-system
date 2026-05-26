export function PageStub({ title, description }: { title: string; description: string }) {
  return (
    <section className="page">
      <header className="page__header">
        <h1 className="page__title">{title}</h1>
        <p className="page__description">{description}</p>
      </header>
      <div className="surface" style={{ padding: 22 }}>
        Страница подготовлена в структуре маршрутов.
      </div>
    </section>
  );
}
