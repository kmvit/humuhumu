import type { ReactNode } from "react";
import { LEGAL_UPDATED } from "../../legal";

// Общий каркас для юридических страниц: заголовок, дата редакции, читаемая типографика.
export default function LegalPage({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <>
      <h1 className="h1">{title}</h1>
      {subtitle && (
        <p className="muted" style={{ marginTop: 6, maxWidth: "72ch" }}>
          {subtitle}
        </p>
      )}
      <p className="muted" style={{ marginTop: 6, fontSize: 13 }}>
        Редакция от {LEGAL_UPDATED}
      </p>
      <div className="legal card" style={{ marginTop: 16 }}>
        {children}
      </div>
    </>
  );
}
