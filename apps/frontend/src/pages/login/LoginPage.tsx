import { FormEvent, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";

import "./LoginPage.css";

export function LoginPage() {
  const { availableUsers, login } = useAuth();
  const [selectedLogin, setSelectedLogin] = useState("orlova_m");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [password, setPassword] = useState("Manager!1");
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
      <form className="login-card surface" onSubmit={onSubmit}>
        <header className="login-card__header">
          <h1>Окно авторизации</h1>
          <p>Введите данные вашей<br />учетной записи:</p>
        </header>

        {error ? <div className="login-card__error">{error}</div> : null}

        <label className="login-field">
          <span>Логин</span>
          <input
            value={selectedLogin}
            onChange={(event) => {
              setSelectedLogin(event.target.value);
              setError("");
            }}
            aria-label="Логин"
            list="login-suggestions"
          />
          <datalist id="login-suggestions">
            <option value="orlova_m" />
            <option value="kuznetsov_m" />
            <option value="ivanov_i" />
            <option value="fedorov_a" />
            {availableUsers.map((user) => (
              <option value={user.login} key={user.id} />
            ))}
          </datalist>
        </label>

        <label className="login-field">
          <span>Пароль</span>
          <div className="login-card__password">
            <input
              type={passwordVisible ? "text" : "password"}
              value={password}
              placeholder="Введите пароль"
              onChange={(event) => {
                setPassword(event.target.value);
                setError("");
              }}
            />
            <button
              className={passwordVisible ? "login-card__eye login-card__eye--open" : "login-card__eye"}
              type="button"
              onClick={() => setPasswordVisible((value) => !value)}
              aria-label="Показать пароль"
            />
          </div>
        </label>

        <button className="login-card__submit" type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Вход..." : "Войти"}
        </button>
      </form>
    </main>
  );
}
