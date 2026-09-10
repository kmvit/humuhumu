import { useState } from "react";
import { patch, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useSite } from "../../site";
import { useToast } from "../../components/ui/Toast";

/** Раздел «Оплата картой» в панели владельца.

    Выключатель отдельный от выбора банка: банк с ключами подключают один
    раз при запуске, а закрыть оплату может понадобиться в любой момент —
    банк лёг, день только за наличные. Включить оплату без доступов он не
    даёт: гость упёрся бы в ошибку банка, а виноватым выглядело бы кафе.
*/
export default function OnlinePayment() {
  const site = useSite();
  const notify = useToast();
  const [saving, setSaving] = useState(false);
  const [local, setLocal] = useState<boolean | null>(null);

  const ready = site?.acquiring_ready ?? false;
  const on = local ?? site?.online_payment_on ?? false;
  const bank = site?.acquiring_name ?? "";
  const live = ready && on; // что реально видит гость

  async function toggle() {
    const next = !on;
    setSaving(true);
    try {
      await patch("/site/", { online_payment_on: next });
      setLocal(next);
      notify(next ? "Оплата картой включена" : "Оплата картой выключена", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h2 className="section-title">Оплата картой</h2>
      <div className="card">
        <div className="between">
          <div>
            <strong className="title">Онлайн-оплата</strong>
            <p className="muted subtitle m-0">
              {live
                ? "Гость видит кнопку «Оплатить картой» в своём заказе"
                : "Гость платит наличными или картой у официанта"}
            </p>
          </div>
          <button
            className={"btn sm" + (on ? "" : " ghost")}
            disabled={saving || !ready}
            onClick={toggle}
          >
            <Icon name={on ? "check" : "close"} size={15} />
            {on ? "Включена" : "Выключена"}
          </button>
        </div>

        <div className="rule-top mt-3">
          <div className="between mt-3">
            <span className="muted sm">Банк</span>
            <span className="inline tight">
              {bank ? <strong>{bank}</strong> : <span className="muted">не выбран</span>}
              <span className={"badge " + (ready ? "ready" : "")}>
                {ready ? "подключён" : "нет доступов"}
              </span>
            </span>
          </div>

          {!ready && (
            <p className="muted sm mt-2 m-0">
              {bank
                ? `Банк выбран, но его ключи не заданы на сервере — оплата картой не заработает, пока их не пропишут. Выключатель до тех пор недоступен.`
                : "Банк не выбран. Подключение эквайринга — разовая настройка при запуске: выбрать банк и прописать его доступы на сервере."}
            </p>
          )}
        </div>
      </div>
    </>
  );
}
