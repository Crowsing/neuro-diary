import "@fontsource/caprasimo/400.css";
import "@fontsource/figtree/400.css";
import "@fontsource/figtree/600.css";
import "@fontsource/figtree/700.css";
import "./styles/organic.css";
import "./styles/app.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { initTelegramShell } from "./telegram/bootstrap";

// Оболонку Telegram піднімаємо ДО першого рендера: і requestFullscreen, і
// підписка на безпечні зони мають бути в дорозі раніше, ніж React намалює
// перший кадр. Поза Telegram виклик не робить нічого.
initTelegramShell({
  webApp: window.Telegram?.WebApp ?? null,
  root: document.documentElement,
  readBackgroundColor: () =>
    getComputedStyle(document.documentElement).getPropertyValue("--color-bg").trim(),
  schedule: (callback, ms) => {
    window.setTimeout(callback, ms);
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
