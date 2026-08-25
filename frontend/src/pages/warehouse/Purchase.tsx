import { useEffect, useMemo, useState } from "react";
import { get, post, patch, del, ApiError } from "../../api";
import type { PurchaseList, PurchaseLine, StockItem } from "../../types";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";

// «2026-08-14» → «14 августа, пятница»
const FMT = new Intl.DateTimeFormat("ru", {
  day: "numeric",
  month: "long",
  weekday: "long",
});

function isoDate(shift: number): string {
  const d = new Date();
  d.setDate(d.getDate() + shift);
  return d.toISOString().slice(0, 10);
}

function fmtQty(q: string | number | null): string {
  if (q === null || q === "") return "0";
  return Number(q).toLocaleString("ru", { maximumFractionDigits: 3 });
}

type Props = {
  items: StockItem[];
  /** Оприходовать купленное: открыть форму прихода с этими строками. */
  onReceive: (lines: { item: number; quantity: number }[]) => void;
};

export default function Purchase({ items, onReceive }: Props) {
  const notify = useToast();
  const [date, setDate] = useState(() => isoDate(1)); // по умолчанию завтра
  const [list, setList] = useState<PurchaseList | null>(null);
  const [loading, setLoading] = useState(true);

  // добавление строки вручную
  const [addOpen, setAddOpen] = useState(false);
  const [addItem, setAddItem] = useState<number | "">("");
  const [addQty, setAddQty] = useState("");

  // правка количества
  const [editId, setEditId] = useState<number | null>(null);
  const [editQty, setEditQty] = useState("");

  async function load(day: string) {
    setLoading(true);
    try {
      setList(await get<PurchaseList>(`/inventory/purchases/day/?date=${day}`));
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось загрузить закуп", "bad");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load(date);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);

  const lines = list?.lines ?? [];
  const left = useMemo(() => lines.filter((l) => !l.is_done).length, [lines]);

  function patchLine(line: PurchaseLine, body: Partial<PurchaseLine>) {
    // Оптимистично: список закупа правят на ходу, ждать ответа незачем.
    setList((l) =>
      l ? { ...l, lines: l.lines.map((x) => (x.id === line.id ? { ...x, ...body } : x)) } : l
    );
    patch(`/inventory/purchase-lines/${line.id}/`, body).catch(() => {
      notify("Не удалось сохранить — обновляю список", "bad");
      load(date);
    });
  }

  async function addLine() {
    if (addItem === "" || !Number(addQty)) {
      notify("Выберите товар и количество", "bad");
      return;
    }
    try {
      await post("/inventory/purchase-lines/", {
        purchase: list?.id,
        item: addItem,
        quantity: Number(addQty),
      });
      setAddQty("");
      setAddOpen(false);
      await load(date);
      notify("Строка добавлена", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Ошибка", "bad");
    }
  }

  async function removeLine(line: PurchaseLine) {
    try {
      await del(`/inventory/purchase-lines/${line.id}/`);
      setList((l) => (l ? { ...l, lines: l.lines.filter((x) => x.id !== line.id) } : l));
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Ошибка", "bad");
    }
  }

  const done = lines.filter((l) => l.is_done);

  return (
    <div className="stack loose mt-4">
      {/* выбор дня */}
      <div className="wrap" style={{ alignItems: "center" }}>
        {[
          { label: "Сегодня", value: isoDate(0) },
          { label: "Завтра", value: isoDate(1) },
        ].map((d) => (
          <button
            key={d.value}
            className={"btn sm " + (date === d.value ? "" : "ghost")}
            onClick={() => setDate(d.value)}
          >
            {d.label}
          </button>
        ))}
        <input
          className="input"
          type="date"
          value={date}
          onChange={(e) => e.target.value && setDate(e.target.value)}
          style={{ width: 160 }}
        />
      </div>

      <div className="between">
        <strong className="title">
          {FMT.format(new Date(date + "T12:00:00"))}
        </strong>
        <span className="muted sm">
          {lines.length === 0
            ? "покупать нечего"
            : `${left} из ${lines.length} осталось купить`}
        </span>
      </div>

      {loading && <div className="skeleton sm" />}

      {!loading && lines.length === 0 && (
        <div className="card center">
          <p className="muted m-0">
            Ничего не заканчивается — список пуст. Строку можно добавить руками.
          </p>
        </div>
      )}

      {lines.map((l) => (
        <div className="row" key={l.id}>
          <button
            className="icon-btn"
            onClick={() => patchLine(l, { is_done: !l.is_done })}
            aria-label={l.is_done ? "Не куплено" : "Куплено"}
            title={l.is_done ? "Вернуть в список" : "Отметить купленным"}
          >
            <Icon name={l.is_done ? "check" : "box"} size={16} />
          </button>
          <div className="row-body">
            <strong className={l.is_done ? "strike" : ""}>
              {l.item_name}
            </strong>
            <span className="muted sm">
              на складе {fmtQty(l.in_stock)} {l.unit_display} · {l.category_name}
              {l.is_auto ? "" : " · вручную"}
            </span>
          </div>

          {editId === l.id ? (
            <span className="inline tight">
              <input
                className="input"
                inputMode="decimal"
                value={editQty}
                onChange={(e) => setEditQty(e.target.value)}
                style={{ width: 92 }}
                autoFocus
              />
              <button
                className="icon-btn"
                onClick={() => {
                  if (Number(editQty) > 0) patchLine(l, { quantity: editQty });
                  setEditId(null);
                }}
                aria-label="Сохранить"
              >
                <Icon name="check" size={16} />
              </button>
            </span>
          ) : (
            <span className="inline">
              <button
                className="chip"
                style={{ minWidth: 84, justifyContent: "center" }}
                onClick={() => {
                  setEditId(l.id);
                  setEditQty(String(Number(l.quantity)));
                }}
                title="Изменить количество"
              >
                {fmtQty(l.quantity)} {l.unit_display}
              </button>
              <button
                className="icon-btn danger"
                onClick={() => removeLine(l)}
                aria-label="Убрать из закупа"
              >
                <Icon name="trash" size={16} />
              </button>
            </span>
          )}
        </div>
      ))}

      {/* добавить свою строку */}
      {addOpen ? (
        <div className="card enter">
          <div className="wrap">
            <select
              className="input grow"
              value={addItem}
              onChange={(e) => setAddItem(Number(e.target.value))}
            >
              <option value="">— товар —</option>
              {items.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name} ({i.unit_display})
                </option>
              ))}
            </select>
            <input
              className="input"
              inputMode="decimal"
              style={{ width: 110 }}
              value={addQty}
              onChange={(e) => setAddQty(e.target.value)}
              placeholder="кол-во"
            />
            <button className="btn sm" onClick={addLine}>
              <Icon name="check" size={16} /> Добавить
            </button>
            <button className="btn sm ghost" onClick={() => setAddOpen(false)}>
              Отмена
            </button>
          </div>
        </div>
      ) : (
        <div className="wrap" style={{ gap: 8 }}>
          <button className="btn sm ghost" onClick={() => setAddOpen(true)}>
            <Icon name="plus" size={15} /> Строка
          </button>
          {done.length > 0 && (
            <button
              className="btn sm"
              onClick={() =>
                onReceive(
                  done.map((l) => ({ item: l.item, quantity: Number(l.quantity) }))
                )
              }
            >
              <Icon name="truck" size={15} /> Оприходовать купленное ({done.length})
            </button>
          )}
        </div>
      )}
    </div>
  );
}
