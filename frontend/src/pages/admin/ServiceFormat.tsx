import type { IconName } from "../../components/Icon";
import Icon from "../../components/Icon";
import { useSite } from "../../site";

// Формат обслуживания: зал со столами или стойка с выдачей по номеру.
// Владельцу только показываем — переключение живёт в Django-админке.
const MODES: Record<"hall" | "counter", { name: string; note: string; icon: IconName }> = {
  hall: {
    name: "Зал с официантами",
    note: "Гость за столом, официант принимает и подаёт",
    icon: "store",
  },
  counter: {
    name: "Стойка и окно",
    note: "Заказ по QR, выдача по номеру, без столов",
    icon: "receipt",
  },
};

/** Карточка «Формат работы» — только показ, формат задаёт тариф. */
export default function ServiceFormat() {
  const site = useSite();
  const serviceMode = site?.service_mode ?? "hall";
  return (
    <>
      <h2 className="section-title">Формат работы</h2>
      <div className="card">
        <div className="between">
          <div className="inline">
            <span className="tx-icon">
              <Icon name={MODES[serviceMode].icon} size={18} />
            </span>
            <div>
              <strong className="title">{MODES[serviceMode].name}</strong>
              <p className="muted subtitle m-0">{MODES[serviceMode].note}</p>
            </div>
          </div>
        </div>
        {/* Переключения здесь нет и быть не может: формат — это тариф,
            «Стойка» стоит дешевле «Зала». Ставится он подпиской, поле
            закрыто и в API (core/serializers.py). */}
        <p className="muted sm mt-3 m-0">
          Формат — это тариф: «Стойка» дешевле «Зала». Чтобы перейти в другой
          формат, напишите в поддержку «Падачи».
        </p>
      </div>

    </>
  );
}
