import { FormEvent, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";

import "./LoginPage.css";

export function LoginPage() {
  const { availableUsers, login } = useAuth();
  const [selectedLogin, setSelectedLogin] = useState(availableUsers[0]?.login ?? "");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const trimmedPassword = password.trim();

    if (!selectedLogin) {
      setError("Выберите логин.");
      return;
    }

    if (trimmedPassword !== "123") {
      setError("Для mock-входа используйте пароль 123.");
      return;
    }

    setError("");
    login(selectedLogin);
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
          <select
            value={selectedLogin}
            onChange={(event) => {
              setSelectedLogin(event.target.value);
              setError("");
            }}
            aria-label="Логин"
          >
            {availableUsers.map((user) => (
              <option value={user.login} key={user.id}>
                {user.login}
              </option>
            ))}
          </select>
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

        <button className="login-card__submit" type="submit">Войти</button>
      </form>
    </main>
  );
}
