import { useEffect, useState, type ReactNode } from "react";
import { get, post } from "../api";
import type { LicenseInfo, Role } from "../types";

/** Подписка «Падачи»: баннеры об оплате и экран блокировки.
 *
 * Статус спрашиваем раз при входе сотрудника (суточную сверку с пультом
 * делает бэкенд, здесь только чтение кэша). Лестница:
 * - «заканчивается» — жёлтая полоска только управленцам (админ, склад):
 *   официанту нечего делать с оплатой, незачем его дёргать;
 * - «просрочено» (грейс) — красная полоска всем сотрудникам;
 * - «заблокировано» — вместо рабочих экранов заглушка с кнопкой
 *   «Проверить оплату»: оплатили → кнопка сама всё вернула.
 * Гостей и меню это не касается — их страницы не оборачиваются.
 */

const MANAGER_ROLES: Role[] = ["admin", "warehouse"];

function fmt(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso + "T00:00:00").toLocaleDateString("ru", {
    day: "numeric",
    month: "long",
  });
}

export default function LicenseGuard({
  role,
  children,
}: {
  role: Role;
  children: ReactNode;
}) {
  const [info, setInfo] = useState<LicenseInfo | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    get<LicenseInfo>("/license/status/")
      .then(setInfo)
      .catch(() => setInfo(null)); // не вышло узнать — работаем молча
  }, []);

  if (!info || !info.enabled || info.status === "active")
    return <>{children}</>;

  if (info.status === "blocked") {
    const refresh = () => {
      setChecking(true);
      post<LicenseInfo>("/license/refresh/", {})
        .then((fresh) => {
          setInfo(fresh);
          if (fresh.status !== "blocked") window.location.reload();
        })
        .catch(() => undefined)
        .finally(() => setChecking(false));
    };
    return (
      <div style={{ textAlign: "center", padding: "64px 16px" }}>
        <h2 style={{ marginBottom: 8 }}>Подписка не оплачена</h2>
        <p style={{ opacity: 0.7, maxWidth: "36em", margin: "0 auto 20px" }}>
          Сервис приостановлен {fmt(info.paid_until)} + {info.grace_days} дн.
          Все данные целы: после оплаты всё вернётся как было. Вопросы — в
          «Падачу».
        </p>
        <button className="btn" onClick={refresh} disabled={checking}>
          {checking ? "Проверяем…" : "Проверить оплату"}
        </button>
      </div>
    );
  }

  const banner =
    info.status === "grace" ? (
      <div className="license-banner danger">
        Подписка просрочена — сервис отключится через несколько дней.
        Оплатите, чтобы не прерывать работу.
      </div>
    ) : MANAGER_ROLES.includes(role) ? (
      <div className="license-banner warn">
        Подписка действует до {fmt(info.paid_until)} — не забудьте оплатить.
      </div>
    ) : null;

  return (
    <>
      {banner}
      {children}
    </>
  );
}
