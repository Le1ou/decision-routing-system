import { departments, workTypes } from "@mocks/mockData";
import { Button } from "@shared/ui";

import "./CreateRequestPage.css";

export function CreateRequestPage() {
  return (
    <section className="create-request-page">
      <form className="create-window">
        <header className="create-window__header">
          <h1>Форма для создания заявки</h1>
          <button type="button" aria-label="Закрыть">×</button>
        </header>

        <div className="create-window__row create-window__row--topic">
          <label>
            Тема:
            <input defaultValue="Сломался компьютер" aria-label="Тема" />
          </label>
        </div>

        <label className="create-window__select-row">
          <span>Отдел:</span>
          <select defaultValue={departments[0]?.id} aria-label="Отдел">
            {departments.map((department) => (
              <option value={department.id} key={department.id}>
                {department.name}
              </option>
            ))}
          </select>
        </label>

        <label className="create-window__select-row">
          <span>Вид работ:</span>
          <select defaultValue={workTypes[0]?.id} aria-label="Вид работ">
            {workTypes.map((workType) => (
              <option value={workType.id} key={workType.id}>
                {workType.name}
              </option>
            ))}
          </select>
        </label>

        <label className="create-window__deadline">
          Срок исполнения:
          <input type="datetime-local" aria-label="Срок исполнения" />
        </label>

        <div className="create-window__description">
          <div className="create-window__description-header">
            <label htmlFor="request-description">Описание проблемы</label>
            <span>До 1000 символов</span>
          </div>
          <textarea
            id="request-description"
            maxLength={1000}
            placeholder="Опишите, что произошло, где находится оборудование и какие признаки неисправности заметили."
          />
        </div>

        <div className="create-window__toolbar">
          <button type="button" aria-label="Настройка шрифта">Aa</button>
          <label aria-label="Прикрепить файлы">
            <span>Прикрепить файлы</span>
            <input type="file" multiple />
          </label>
        </div>

        <footer className="create-window__footer">
          <Button type="button" variant="ghost">Отправить</Button>
        </footer>
      </form>
    </section>
  );
}
