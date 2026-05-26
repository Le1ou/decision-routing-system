import { requests } from "@mocks/mockData";
import { priorityLabels, statusLabels } from "@shared/model/labels";

import "./RequestsPage.css";

export function RequestsPage() {
  const selectedRequest = requests[0];

  return (
    <section className="requests-page">
      <aside className="requests-sidebar">
        <div className="requests-toolbar">
          <select defaultValue="priority" aria-label="Сортировка">
            <option value="priority">Сортировать по Приоритет</option>
            <option value="status">Сортировать по Статус</option>
            <option value="createdAt">Сортировать по Дата создания</option>
            <option value="finishedAt">Сортировать по Дата закрытия</option>
          </select>
          <button type="button" aria-label="Направление сортировки">↕</button>
        </div>

        <div className="requests-list">
          {requests.map((request) => (
            <article className="request-row" key={request.id}>
              <strong>Заявка № {request.number.replace("DRS-", "")}</strong>
              <span>{request.title}</span>
            </article>
          ))}
        </div>
      </aside>

      <article className="request-card">
        <button className="request-card__edit" type="button" aria-label="Редактировать">✎</button>

        <header className="request-card__title">
          <h1>Департамент информационных технологий/ Заявка № {selectedRequest.number.replace("DRS-", "")}</h1>
          <input defaultValue={`${selectedRequest.title}  *Поле с указанием темы заявки*`} aria-label="Тема заявки" />
        </header>

        <div className="request-card__main">
          <section className="request-card__workarea">
            <div className="request-card__section-header">
              <strong>Описание</strong>
              <span>Предыдущий исполнитель: не назначен</span>
            </div>
            <textarea
              defaultValue="После перезапуска рабочая станция не проходит загрузку и не подключается к сети цеха."
              aria-label="Описание заявки"
            />
            <label className="request-card__comment">
              <span>Комментарий исполнителя:</span>
              <textarea placeholder="Комментарий появится после назначения или выполнения работ" aria-label="Комментарий исполнителя" />
            </label>
          </section>

          <aside className="request-card__info">
            <div className="request-card__params">
              <label>
                Статус:
                <select defaultValue={selectedRequest.status}>
                  <option value={selectedRequest.status}>{statusLabels[selectedRequest.status]}</option>
                </select>
              </label>
              <label>
                Приоритет:
                <input defaultValue={priorityLabels[selectedRequest.priority]} />
              </label>
              <label>
                Вид работ:
                <input defaultValue="Починка оборудования" />
              </label>
            </div>

            <section className="request-info-box">
              <h2>Автор заявки</h2>
              <p><b>ФИО:</b> Иванов Иван Иванович</p>
              <p><b>Отдел:</b> Бухгалтерия</p>
              <p><b>Должность:</b> Специалист</p>
            </section>

            <section className="request-info-box">
              <h2>Исполнитель</h2>
              <p><b>ФИО:</b> -</p>
              <p><b>Отдел:</b> -</p>
              <p><b>Должность:</b> -</p>
            </section>

            <section className="request-info-box request-info-box--dates">
              <h2>Информация о заявке</h2>
              <p><b>Дата и время последнего изменения:</b> 04.05.2026, 12:02</p>
              <p><b>Дата и время создания заявки:</b> 04.05.2026, 12:02</p>
              <p><b>Дата и время назначения исполнителя:</b> -</p>
              <p><b>Дата и время взятия в работу заявки:</b> -</p>
              <p><b>Дата и время закрытия заявки:</b> -</p>
            </section>
          </aside>
        </div>
      </article>
    </section>
  );
}
