import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme.css";
import "./app.css";
import "./screens.css";
import { App } from "./App";
import { initStore } from "./store";

initStore();
// JARVIS spends most of its time minimised (listening for the wake word), and its window is started with background
// throttling off so it can listen and speak there. Pause the purely decorative animations while nobody can see them.
const syncHidden = () => document.documentElement.classList.toggle("page-hidden", document.hidden);
document.addEventListener("visibilitychange", syncHidden);
syncHidden();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
