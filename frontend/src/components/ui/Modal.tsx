import { useEffect, type ReactNode } from "react";

// Единое модальное окно: по центру (variant="center") или нижний лист
// (variant="sheet"). Закрывается по Escape и клику по подложке.
// head — необязательная шапка с рамкой; тело прокручивается само.
export default function Modal({
  onClose,
  variant = "center",
  head,
  children,
}: {
  onClose: () => void;
  variant?: "center" | "sheet";
  head?: ReactNode;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className={"modal" + (variant === "sheet" ? " sheet" : "")}
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-panel">
        {head && <div className="modal-head">{head}</div>}
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
