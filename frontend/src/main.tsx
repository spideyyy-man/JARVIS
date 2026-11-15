import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme.css";
import "./app.css";
import "./screens.css";
import { App } from "./App";
import { initStore } from "./store";

initStore();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
