import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import Icon from "../Icon";

type ToastKind = "plain" | "ok" | "bad";
type Notify = (message: string, kind?: ToastKind) => void;

const ToastContext = createContext<Notify>(() => {});

// Единый тост приложения: один экземпляр на всё приложение, единый таймаут,
// рисуется поверх любых окон. Страницы зовут useToast() вместо своего state.
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ text: string; kind: ToastKind } | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const notify = useCallback<Notify>((text, kind = "plain") => {
    window.clearTimeout(timer.current);
    setToast({ text, kind });
    timer.current = window.setTimeout(() => setToast(null), 3000);
  }, []);

  return (
    <ToastContext.Provider value={notify}>
      {children}
      {toast && (
        <div
          className={"toast" + (toast.kind === "plain" ? "" : " " + toast.kind)}
          role="status"
        >
          <Icon name="spark" size={18} /> {toast.text}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
