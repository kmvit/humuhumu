import { useState } from "react";
import { patch, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useSite, useFeature } from "../../site";
import { useToast } from "../../components/ui/Toast";
import Stepper from "../../components/ui/Stepper";

/** Раздел «Бонусы» в панели владельца.

    Бонусная программа входит в тариф «Максимум» — на других тарифах раздел
    не показываем совсем: включать нечего, бэкенд всё равно откажет.
*/
export default function Bonuses() {
  const site = useSite();
  const hasLoyalty = useFeature("loyalty");
  const notify = useToast();
  const [saving, setSaving] = useState(false);
  // Локальное состояние перекрывает контекст: он грузится один раз при старте
  // и после сохранения показывал бы прежние значения.
  const [local, setLocal] = useState<{
    enabled?: boolean;
    welcome?: number;
    percent?: number;
    waiter?: boolean;
    guest?: boolean;
  }>({});

  if (!hasLoyalty) return null;

  const enabled = local.enabled ?? site?.bonus_enabled ?? false;
  const welcome = local.welcome ?? site?.bonus_welcome ?? 200;
  const percent = local.percent ?? Number(site?.bonus_earn_percent ?? 5);
  const byWaiter = local.waiter ?? site?.bonus_redeem_waiter ?? true;
  const byGuest = local.guest ?? site?.bonus_redeem_guest ?? false;

  async function save(body: Record<string, unknown>, patchLocal: typeof local, ok: string) {
    setSaving(true);
    try {
      await patch("/site/", body);
      setLocal((s) => ({ ...s, ...patchLocal }));
      notify(ok, "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  // Приветственные и процент меняются шагами — сохраняем сразу, без «Применить»:
  // так же ведут себя остальные настройки в панели.
  const saveWelcome = (v: number) =>
    save({ bonus_welcome: v }, { welcome: v }, `Приветственные: ${v} бонусов`);
  const savePercent = (v: number) =>
    save({ bonus_earn_percent: v }, { percent: v }, `Начисление: ${v}% с чека`);

  return (
    <>
      <h2 className="section-title">Бонусы</h2>
      <div className="card">
        <div className="between">
          <div>
            <strong className="title">Бонусная программа</strong>
            <p className="muted subtitle m-0">
              1 бонус = 1 ₽. Гость копит с покупок и платит бонусами часть счёта или весь.
            </p>
          </div>
          <button
            className={"btn sm" + (enabled ? "" : " ghost")}
            disabled={saving}
            onClick={() =>
              save(
                { bonus_enabled: !enabled },
                { enabled: !enabled },
                !enabled ? "Бонусная программа включена" : "Бонусная программа выключена"
              )
            }
          >
            <Icon name={enabled ? "check" : "close"} size={15} />
            {enabled ? "Включена" : "Выключена"}
          </button>
        </div>

        {enabled && (
          <>
            <div className="rule-top mt-3">
              <div className="between mt-3">
                <div>
                  <strong>Приветственные бонусы</strong>
                  <div className="muted sm">Разово при регистрации гостя</div>
                </div>
                <Stepper
                  value={welcome}
                  width={132}
                  onDec={() => saveWelcome(Math.max(0, welcome - 50))}
                  onInc={() => saveWelcome(Math.min(5000, welcome + 50))}
                />
              </div>

              <div className="between mt-3">
                <div>
                  <strong>Начисление с покупки</strong>
                  <div className="muted sm">
                    Процент от суммы, оплаченной деньгами · с чека 1000 ₽ это{" "}
                    {Math.floor((1000 * percent) / 100)} бонусов
                  </div>
                </div>
                <Stepper
                  value={percent + " %"}
                  width={132}
                  onDec={() => savePercent(Math.max(0, percent - 1))}
                  onInc={() => savePercent(Math.min(50, percent + 1))}
                />
              </div>
            </div>

            <div className="rule-top mt-3">
              <div className="muted sm mt-3">Кто может списывать бонусы:</div>
              <div className="wrap mt-2">
                <button
                  className={"btn sm" + (byWaiter ? "" : " ghost")}
                  disabled={saving}
                  onClick={() =>
                    save(
                      { bonus_redeem_waiter: !byWaiter },
                      { waiter: !byWaiter },
                      !byWaiter ? "Официант списывает бонусы" : "Официанту списание закрыто"
                    )
                  }
                >
                  <Icon name={byWaiter ? "check" : "close"} size={15} /> Официант на кассе
                </button>
                <button
                  className={"btn sm" + (byGuest ? "" : " ghost")}
                  disabled={saving}
                  onClick={() =>
                    save(
                      { bonus_redeem_guest: !byGuest },
                      { guest: !byGuest },
                      !byGuest ? "Гость списывает сам" : "Гостю списание закрыто"
                    )
                  }
                >
                  <Icon name={byGuest ? "check" : "close"} size={15} /> Гость в приложении
                </button>
              </div>
              <p className="muted sm mt-2 m-0">
                Официант находит гостя по телефону на закрытии счёта. Гость применяет
                бонусы сам — в своём заказе, если вошёл в приложение.
              </p>
              {!byWaiter && !byGuest && (
                <p className="muted sm mt-2 m-0">
                  Сейчас списать бонусы не может никто — гости продолжат их копить.
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </>
  );
}
