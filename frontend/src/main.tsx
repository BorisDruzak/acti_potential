import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import "./styles.css";
import "./components/workspace.css";
import "./unified-interface.css";

createRoot(document.getElementById("root")!).render(
  <AuthProvider>
    <App />
  </AuthProvider>,
);
