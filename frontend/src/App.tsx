import { useAuth } from "./auth/AuthProvider";
import { Login } from "./pages/Login";
import { Workspace } from "./pages/Workspace";
import { Password } from "./pages/Password";

export function App() {
  const auth = useAuth();
  if (auth.status === "loading")
    return (
      <main className="state-screen">
        <div className="brand dark">
          ACTI<span>v3</span>
        </div>
        <p role="status">Подключение к рабочему пространству…</p>
      </main>
    );
  if (auth.status === "unavailable")
    return (
      <main className="state-screen">
        <div className="brand dark">
          ACTI<span>v3</span>
        </div>
        <h1>Сервис временно недоступен</h1>
        <p role="alert">{auth.error}</p>
        <button className="primary" onClick={() => void auth.refresh()}>
          Повторить подключение
        </button>
      </main>
    );
  return auth.status === "authenticated" ? (
    auth.user?.must_change_password ? (
      <Password required />
    ) : (
      <Workspace />
    )
  ) : (
    <Login />
  );
}
