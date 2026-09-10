import { useEffect, useState } from "react";
import { get, post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite } from "../../site";
import type { LicenseInfo, Plan } from "../../types";

/** Раздел «Подписка» в панели владельца: тариф и состояние оплаты.

    Раньше про подписку владелец узнавал только из тревожной полоски, когда
    срок уже поджимал. Здесь то же самое, но спокойно и в любой момент —
    вместе с тем, что входит в его тариф.
*/

const PLAN_NAME: Record<Plan, string> = {
  start: "Старт",
  hall: "Зал",
  max: "Максимум",
};

const PLAN_NOTE: Record<Plan, string> = {
  start: "Меню, QR-заказ, столы или стойка, оплаты, отчёты",
  hall: "Всё из «Старта» + экраны кухни и бара",
  max: "Всё из «Зала» + склад, смены и зарплата, финансы, бонусы",
};

const STATUS: Record<LicenseInfo["status"], { label: string; cls: string }> = {
  active: { label: "оплачена", cls: "ready" },
  expiring: { label: "заканчивается", cls: "preparing" },
  grace: { label: "просрочена", cls: "cancelled" },
  blocked: { label: "заблокирована", cls: "cancelled" },
};

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return d.toLocaleDateString("ru", { day: "numeric", month: "long", year: "numeric" });
}

export default function Subscription() {
  const site = useSite();
  const notify = useToast();
  const [info, setInfo] = useState<LicenseInfo | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    get<LicenseInfo>("/license/status/")
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);

  // тариф знаем из настроек даже без лицензии — он определяет доступные разделы
  const plan = (info?.plan ?? site?.plan ?? "start") as Plan;

  async function refresh() {
    setChecking(true);
    try {
      const fresh = await post<LicenseInfo>("/license/refresh/", {});
      setInfo(fresh);
      notify("Статус обновлён", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось проверить", "bad");
    } finally {
      setChecking(false);
    }
  }

  return (
    <>
      <h2 className="section-title">Подписка</h2>
      <div className="card">
        <div className="between">
          <div>
            <strong className="title">Тариф «{PLAN_NAME[plan]}»</strong>
            <p className="muted subtitle m-0">{PLAN_NOTE[plan]}</p>
          </div>
          {info?.enabled && (
            <span className={"badge " + STATUS[info.status].cls}>
              {STATUS[info.status].label}
            </span>
          )}
        </div>

        {info?.enabled ? (
          <div className="rule-top mt-3">
            <div className="between mt-3">
              <span className="muted sm">Оплачено до</span>
              <strong>{fmtDate(info.paid_until)}</strong>
            </div>
            <div className="between mt-2">
              <span className="muted sm">Последняя сверка</span>
              <span className="muted">{fmtDate(info.checked_at)}</span>
            </div>
            {info.status !== "active" && (
              <p className="muted sm mt-2 m-0">
                {info.status === "expiring"
                  ? "Продлите подписку, чтобы работа не прервалась."
                  : `После оплаты нажмите «Проверить» — доступ вернётся сразу. Дней отсрочки: ${info.grace_days}.`}
              </p>
            )}
            <button className="btn sm ghost block mt-3" disabled={checking} onClick={refresh}>
              <Icon name={checking ? "spark" : "check"} size={15} /> Проверить оплату
            </button>
          </div>
        ) : (
          <p className="muted sm mt-3 m-0">
            Установка работает без подписки — тариф задан вручную. Смена тарифа
            и подключение лицензии — на стороне «Падачи».
          </p>
        )}
      </div>
    </>
  );
}
