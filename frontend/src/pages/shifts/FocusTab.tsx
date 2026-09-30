import { useCallback, useEffect, useState } from "react";
import { del, get, patch, post, ApiError } from "../../api";
import type { FocusItemInfo, FocusPayload } from "../../types";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";

const fmt = (v: string | number) =>
  Number(v).toLocaleString("ru", { maximumFractionDigits: 2 });

// «2026-10-06» → «6 окт»
const fmtDate = (iso: string) =>
  new Date(iso.slice(0, 10) + "T00:00").toLocaleDateString("ru", {
    day: "numeric",
    month: "short",
  });

// местная дата в «ГГГГ-ММ-ДД» (toISOString сдвинул бы день по UTC)
function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// Фокусные позиции: что сейчас продвигаем и сколько за это платим.
// Видит весь персонал — им продавать; заводит и правит менеджер или админ.
export default function FocusTab({ onChanged }: { onChanged: () => void }) {
  const notify = useToast();
  const [data, setData] = useState<FocusPayload | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    get<FocusPayload>("/shifts/focus/").then(setData).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function run(fn: () => Promise<FocusPayload>, ok: string) {
    setBusy(true);
    try {
      setData(await fn());
      notify(ok, "ok");
      onChanged();
      return true;
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не получилось", "bad");
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <p className="muted mt-3">Загрузка…</p>;

  return (
    <div className="card mt-3">
      <div className="between">
        <strong className="title">Фокусные позиции</strong>
        {data.can_edit && !adding && (
          <button className="btn sm" onClick={() => setAdding(true)}>
            <Icon name="plus" size={15} /> Добавить
          </button>
        )}
      </div>
      <p className="muted subtitle m-0">
        За каждую проданную штуку — надбавка тому, кто отмечен исполнителем заказа.
        Предлагайте, когда это действительно подходит гостю.
      </p>

      {adding && data.products && (
        <AddForm
          products={data.products}
          busy={busy}
          onCancel={() => setAdding(false)}
          onSave={async (body) => {
            if (await run(() => post<FocusPayload>("/shifts/focus/", body), "Позиция в фокусе")) {
              setAdding(false);
            }
          }}
        />
      )}

      {data.items.map((item) => (
        <FocusRow
          key={item.id}
          item={item}
          canEdit={data.can_edit}
          busy={busy}
          onSave={(body) =>
            run(() => patch<FocusPayload>(`/shifts/focus/${item.id}/`, body), "Сохранено")
          }
          onRemove={() =>
            run(() => del<FocusPayload>(`/shifts/focus/${item.id}/`), "Снято с фокуса")
          }
        />
      ))}
      {data.items.length === 0 && !adding && (
        <p className="muted mt-3">
          {data.can_edit
            ? "Сейчас ничего не в фокусе. Добавьте позиции на неделю."
            : "Сейчас фокусных позиций нет."}
        </p>
      )}
    </div>
  );
}

function FocusRow({
  item,
  canEdit,
  busy,
  onSave,
  onRemove,
}: {
  item: FocusItemInfo;
  canEdit: boolean;
  busy: boolean;
  onSave: (body: { bonus: string; date_to: string }) => Promise<boolean>;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [bonus, setBonus] = useState(String(Number(item.bonus)));
  const [dateTo, setDateTo] = useState(item.date_to);
  // снятие в два нажатия: нативный confirm в киоск-браузерах подавляется
  const [confirm, setConfirm] = useState(false);

  return (
    <div className="row">
      <span className="tx-icon">
        <Icon name="spark" size={17} />
      </span>
      <div className="row-body">
        <strong>{item.title}</strong>
        <span className="muted">
          {item.active
            ? `до ${fmtDate(item.date_to)} включительно`
            : `с ${fmtDate(item.starts_at)} по ${fmtDate(item.date_to)}`}
        </span>
        {editing && (
          <div className="wrap mt-2" style={{ alignItems: "flex-end" }}>
            <label className="field m-0" style={{ flex: "1 1 110px" }}>
              <span className="label">₽ за штуку</span>
              <input
                className="input"
                type="number"
                min={1}
                step="10"
                value={bonus}
                onChange={(e) => setBonus(e.target.value)}
              />
            </label>
            <label className="field m-0" style={{ flex: "1 1 150px" }}>
              <span className="label">По</span>
              <input
                className="input"
                type="date"
                min={isoDay(new Date())}
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
              />
            </label>
            <button
              className="btn sm"
              disabled={busy}
              onClick={async () => {
                if (await onSave({ bonus, date_to: dateTo })) setEditing(false);
              }}
            >
              <Icon name="check" size={15} /> Сохранить
            </button>
            <button className="btn ghost sm" onClick={() => setEditing(false)}>
              Отмена
            </button>
          </div>
        )}
        {editing && item.active && (
          <span className="muted sm mt-1">
            Новая сумма — с этой минуты. Проданное раньше остаётся по старой.
          </span>
        )}
      </div>
      <strong className="num">+{fmt(item.bonus)} ₽</strong>
      {canEdit && !editing && (
        <>
          <button
            className="icon-btn"
            aria-label="Изменить"
            disabled={busy}
            onClick={() => setEditing(true)}
          >
            <Icon name="edit" size={15} />
          </button>
          {confirm ? (
            <button className="btn sm danger" disabled={busy} onClick={onRemove}>
              Снять
            </button>
          ) : (
            <button
              className="icon-btn"
              aria-label="Снять с фокуса"
              disabled={busy}
              onClick={() => setConfirm(true)}
            >
              <Icon name="minus" size={16} />
            </button>
          )}
        </>
      )}
    </div>
  );
}

function AddForm({
  products,
  busy,
  onCancel,
  onSave,
}: {
  products: NonNullable<FocusPayload["products"]>;
  busy: boolean;
  onCancel: () => void;
  onSave: (body: Record<string, unknown>) => void;
}) {
  const today = isoDay(new Date());
  const [product, setProduct] = useState<number | "">("");
  const [variant, setVariant] = useState<number | "">("");
  const [bonus, setBonus] = useState("");
  const [from, setFrom] = useState(today);
  // по умолчанию — неделя, как в правилах Монти
  const [to, setTo] = useState(() => isoDay(new Date(Date.now() + 6 * 86400000)));
  const variants = products.find((p) => p.id === product)?.variants ?? [];

  return (
    <div className="rule-top mt-3 pt-3">
      <label className="field">
        <span className="label">Позиция меню</span>
        <select
          className="input"
          value={product}
          onChange={(e) => {
            setProduct(e.target.value === "" ? "" : Number(e.target.value));
            setVariant("");
          }}
        >
          <option value="">Выберите…</option>
          {products.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      {variants.length > 0 && (
        <label className="field">
          <span className="label">Объём</span>
          <select
            className="input"
            value={variant}
            onChange={(e) => setVariant(e.target.value === "" ? "" : Number(e.target.value))}
          >
            <option value="">Любой</option>
            {variants.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="wrap">
        <label className="field" style={{ flex: "1 1 110px" }}>
          <span className="label">₽ за штуку</span>
          <input
            className="input"
            type="number"
            min={1}
            step="10"
            value={bonus}
            onChange={(e) => setBonus(e.target.value)}
          />
        </label>
        <label className="field" style={{ flex: "1 1 140px" }}>
          <span className="label">С</span>
          <input
            className="input"
            type="date"
            min={today}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label className="field" style={{ flex: "1 1 140px" }}>
          <span className="label">По</span>
          <input
            className="input"
            type="date"
            min={from}
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
      </div>
      <div className="wrap">
        <button
          className="btn sm"
          disabled={busy || product === "" || !bonus}
          onClick={() =>
            onSave({
              product,
              variant: variant === "" ? null : variant,
              bonus,
              date_from: from,
              date_to: to,
            })
          }
        >
          <Icon name="check" size={15} /> Добавить
        </button>
        <button className="btn ghost sm" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </div>
  );
}
