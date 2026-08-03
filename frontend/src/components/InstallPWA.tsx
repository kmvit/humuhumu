import { useEffect, useState } from "react";
import Icon from "./Icon";

// Событие Chrome/Edge, которое даёт показать нативный диалог установки.
type InstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

// Приложение уже открыто как установленное (со своего значка) — кнопка не нужна.
function isStandalone(): boolean {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    // iOS Safari
    (navigator as unknown as { standalone?: boolean }).standalone === true
  );
}

function isIOS(): boolean {
  return /iphone|ipad|ipod/i.test(navigator.userAgent);
}

// Кнопка «Установить приложение».
// Android/десктоп (Chrome/Edge) — нативный диалог через beforeinstallprompt.
// iOS Safari — своего диалога нет, показываем подсказку «Поделиться → На экран „Домой“».
export default function InstallPWA() {
  const [deferred, setDeferred] = useState<InstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(isStandalone());
  const [showIosHint, setShowIosHint] = useState(false);

  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault(); // не показываем стандартный баннер — покажем по кнопке
      setDeferred(e as InstallPromptEvent);
    };
    const onInstalled = () => {
      setInstalled(true);
      setDeferred(null);
      setShowIosHint(false);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (installed) return null;

  const ios = isIOS();
  // Показываем кнопку, только когда установка реально возможна:
  // либо есть отложенный диалог (Android/десктоп), либо это iOS (ручная установка).
  if (!deferred && !ios) return null;

  async function install() {
    if (deferred) {
      await deferred.prompt();
      await deferred.userChoice;
      setDeferred(null); // повторно один и тот же prompt использовать нельзя
    } else {
      setShowIosHint(true);
    }
  }

  return (
    <>
      <button
        className="icon-btn"
        onClick={install}
        aria-label="Установить приложение"
        title="Установить приложение"
      >
        <Icon name="download" size={18} />
      </button>

      {showIosHint && (
        <div
          className="install-hint-overlay"
          role="dialog"
          aria-modal="true"
          onClick={() => setShowIosHint(false)}
        >
          <div className="install-hint" onClick={(e) => e.stopPropagation()}>
            <span className="brand" style={{ fontSize: 20 }}>
              <span className="logo">
                <Icon name="coffee" size={17} />
              </span>
              <span className="brand-name">Установить на экран «Домой»</span>
            </span>
            <p className="muted" style={{ margin: 0 }}>
              Нажмите{" "}
              <span className="tx-icon" style={{ width: 26, height: 26, verticalAlign: "middle" }}>
                <Icon name="share" size={14} />
              </span>{" "}
              «Поделиться» внизу Safari, затем выберите «На экран „Домой“».
            </p>
            <button className="btn sm" onClick={() => setShowIosHint(false)}>
              Понятно
            </button>
          </div>
        </div>
      )}
    </>
  );
}
