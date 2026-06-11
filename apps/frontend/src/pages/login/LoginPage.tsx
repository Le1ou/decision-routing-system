import { FormEvent, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";

import "./LoginPage.css";

const showDemoCredentials = import.meta.env.DEV;

export function LoginPage() {
  const { login } = useAuth();
  const [selectedLogin, setSelectedLogin] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const trimmedLogin = selectedLogin.trim();

    if (!trimmedLogin) {
      setError("Выберите логин.");
      return;
    }

    if (!password) {
      setError("Введите пароль.");
      return;
    }

    setError("");
    setIsSubmitting(true);

    try {
      await login({ login: trimmedLogin, password });
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Ошибка авторизации.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <form className="login-card surface" onSubmit={onSubmit} autoComplete="off">
        <header className="login-card__header">
          <img src="/application-dispatcher-mark.svg" alt="" aria-hidden="true" />
          <span>ДиспетчерЗаявок</span>
          <h1>Окно авторизации</h1>
          <p>Введите данные вашей<br />учетной записи:</p>
        </header>

        {error ? <div className="login-card__error">{error}</div> : null}

        <label className="login-field">
          <span className="login-field__label">
            Логин
            {showDemoCredentials ? (
              <span className="login-field__hint" tabIndex={0} aria-label="Демо-доступы">
                ?
                <span className="login-field__tooltip" role="tooltip">
                  <b>Демо-доступы</b>
                  <span>Топ-менеджер: orlova_m / Manager!1</span>
                  <span>Руководитель: kuznetsov_m / Kuznetsov!7</span>
                  <span>Исполнитель: ivanov_i / SecretPassword!1</span>
                  <span>Автор: fedorov_a / Fedorov!6</span>
                </span>
              </span>
            ) : null}
          </span>
          <input
            value={selectedLogin}
            onChange={(event) => {
              setSelectedLogin(event.target.value);
              setError("");
            }}
            aria-label="Логин"
            autoComplete="off"
          />
        </label>

        <label className="login-field">
          <span>Пароль</span>
          <div className="login-card__password">
            <input
              type={passwordVisible ? "text" : "password"}
              value={password}
              placeholder="Пароль"
              autoComplete="off"
              onChange={(event) => {
                setPassword(event.target.value);
                setError("");
              }}
            />
            <button
              className={passwordVisible ? "login-card__eye login-card__eye--open" : "login-card__eye"}
              type="button"
              onClick={() => setPasswordVisible((value) => !value)}
              aria-label={passwordVisible ? "Скрыть пароль" : "Показать пароль"}
            >
              {passwordVisible ? "Скрыть" : "Показать"}
            </button>
          </div>
        </label>

        <button className="login-card__submit" type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Вход..." : "Войти"}
        </button>
      </form>
    </main>
  );
}
