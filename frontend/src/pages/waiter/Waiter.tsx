import { useEffect, useMemo, useRef, useState } from "react";
import { get, patch, post, ApiError } from "../../api";
import { useToast } from "../../components/ui/Toast";
import type { Order, OrderItem, PayMethod, Station, StationStatus, Table } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";
import { playChime } from "../../sound";
import { fmtClock, fmtDuration, minutesBetween } from "../../time";
import Compose from "./Compose";
import Modal from "../../components/ui/Modal";
import Stepper from "../../components/ui/Stepper";

const STATUS_LABEL: Record<StationStatus, string> = {
  new: "новый",
  in_progress: "готовится",
  ready: "готово",
};
const STATUS_CLASS: Record<StationStatus, string> = {
  new: "open",
  in_progress: "preparing",
  ready: "ready",
};

// дев-эмуляция результата терминала; в бою результат приходит вебхуком провайдера
const DEV = import.meta.env.DEV;

export default function Waiter() {
  const { orders, reload } = useLiveOrders("/orders/?status=open", { sound: false });
  // «К подаче»: готовые, но не отнесённые станции — открытые + оплаченные (оплата вперёд)
  const { orders: serveOrders, reload: reloadServe } =
    useLiveOrders("/orders/?serve=1", { sound: false });
  // заявки от клиентов (без стола) — со звуком, чтобы официант заметил
  const { orders: requests, highlight: reqHighlight, reload: reloadRequests } =
    useLiveOrders("/orders/?status=requested", { sound: true });
  const [selected, setSelected] = useState<string | null>(null);
  const [composeFor, setComposeFor] = useState<string | null>(null);
  const [addFor, setAddFor] = useState<Order | null>(null);
  const [closing, setClosing] = useState(false);
  const [busyItem, setBusyItem] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  // код на удаление позиции, которую кухня/бар уже готовят (см. настройки заведения)
  const [codeFor, setCodeFor] = useState<number | null>(null);
  const [codeText, setCodeText] = useState("");
  const [view, setView] = useState<"open" | "closed">("open");
  const [closed, setClosed] = useState<Order[]>([]);
  const [splitN, setSplitN] = useState(2);
  const [busyGuest, setBusyGuest] = useState<number | null>(null);
  const [commentEdit, setCommentEdit] = useState<number | null>(null);
  const [commentText, setCommentText] = useState("");
  const [openClosed, setOpenClosed] = useState<Set<number>>(new Set());
  const [tables, setTables] = useState<string[]>([]);
  const [busyReq, setBusyReq] = useState<number | null>(null);
  const [moveFor, setMoveFor] = useState<number | null>(null);
  // выбранные для переноса позиции; пусто = переносим заказ целиком
  const [moveSel, setMoveSel] = useState<Set<number>>(new Set());
  const [busyMove, setBusyMove] = useState<number | null>(null);
  const [busyClose, setBusyClose] = useState<number | null>(null);
  // какой счёт сейчас на выборе способа оплаты: id заказа или "table" (весь стол)
  const [payFor, setPayFor] = useState<number | "table" | null>(null);
  const [busyServe, setBusyServe] = useState<string | null>(null);
  const seenServe = useRef<Set<string> | null>(null);
  const notify = useToast();

  useEffect(() => {
    get<Table[]>("/tables/").then((ts) => setTables(ts.map((t) => t.name))).catch(() => {});
  }, []);

  const toggleClosed = (id: number) =>
    setOpenClosed((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  useEffect(() => {
    if (view === "closed") get<Order[]>("/orders/?status=paid&closed=today").then(setClosed).catch(() => {});
  }, [view]);

  // при открытии стола подставляем N = число уже отмеченных гостей (минимум 2)
  useEffect(() => {
    if (!selected) return;
    const os = byTable[selected] ?? [];
    const maxG = Math.max(0, ...os.flatMap((o) => o.items.map((i) => i.guest ?? 0)));
    setSplitN(Math.max(2, maxG));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  // подтверждение прямо в UI — нативный confirm() в киоск/встроенных браузерах подавляется
  async function removeItem(order: Order, item: OrderItem, code?: string) {
    setBusyItem(item.id);
    try {
      await post(`/orders/${order.id}/remove_item/`, {
        item_id: item.id,
        ...(code ? { code } : {}),
      });
      setConfirmId(null);
      setCodeFor(null);
      setCodeText("");
      await reload();
    } catch (e) {
      // код неверный/не задан — оставляем панель открытой, чтобы попробовать ещё раз
      notify(e instanceof ApiError ? e.message : "Не удалось убрать позицию", "bad");
    } finally {
      setBusyItem(null);
    }
  }

  // «+» у позиции: увеличить её количество на 1
  async function changeQty(order: Order, item: OrderItem, delta: number) {
    const next = item.quantity + delta;
    if (next < 1) return; // меньше 1 — только через удаление
    setBusyItem(item.id);
    try {
      await patch(`/orders/${order.id}/item_qty/`, { item_id: item.id, quantity: next });
      await reload();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось изменить количество", "bad");
    } finally {
      setBusyItem(null);
    }
  }

  // разбиение счёта: тап по позиции меняет её гостя (0 = общий, дальше 1..splitN)
  async function cycleGuest(order: Order, item: OrderItem) {
    const cur = item.guest ?? 0;
    const next = cur >= splitN ? 0 : cur + 1;
    setBusyGuest(item.id);
    try {
      await patch(`/orders/${order.id}/item_guest/`, { item_id: item.id, guest: next || null });
      await reload();
    } finally {
      setBusyGuest(null);
    }
  }

  const byTable = useMemo(() => {
    const m: Record<string, Order[]> = {};
    for (const o of orders) (m[o.table] ||= []).push(o);
    return m;
  }, [orders]);

  // «К подаче»: готовые, но ещё не отнесённые станции (кухня/бар отдельно)
  const serveList = useMemo(() => {
    const rows: { order: Order; station: Station }[] = [];
    for (const o of serveOrders) {
      if (o.has_food && o.food_status === "ready" && !o.food_served)
        rows.push({ order: o, station: "kitchen" });
      if (o.has_drinks && o.drinks_status === "ready" && !o.drinks_served)
        rows.push({ order: o, station: "bar" });
    }
    return rows;
  }, [serveOrders]);

  // звук + без повторов: пикаем, когда появляется новая готовая станция
  useEffect(() => {
    const keys = new Set(serveList.map((r) => `${r.order.id}:${r.station}`));
    if (seenServe.current === null) {
      seenServe.current = keys; // первая загрузка — не сигналим
      return;
    }
    const hasNew = [...keys].some((k) => !seenServe.current!.has(k));
    if (hasNew) playChime();
    seenServe.current = keys;
  }, [serveList]);

  async function serveStation(order: Order, station: Station) {
    const key = `${order.id}:${station}`;
    setBusyServe(key);
    try {
      await patch(`/orders/${order.id}/serve/`, { station });
      await Promise.all([reloadServe(), reload()]);
    } finally {
      setBusyServe(null);
    }
  }

  if (composeFor) {
    return (
      <Compose
        table={composeFor}
        onCreated={() => {
          setComposeFor(null);
          reload();
        }}
        onCancel={() => setComposeFor(null)}
      />
    );
  }

  if (addFor) {
    const maxG = Math.max(0, ...addFor.items.map((i) => i.guest ?? 0));
    return (
      <Compose
        table={addFor.table}
        orderId={addFor.id}
        initialGuests={maxG}
        onCreated={() => {
          setAddFor(null);
          reload();
        }}
        onCancel={() => setAddFor(null)}
      />
    );
  }

  const selOrders = selected ? byTable[selected] ?? [] : [];
  const selTotal = selOrders.reduce((s, o) => s + Number(o.total), 0);

  // разбивка суммы по гостям (0 = общий)
  const breakdownOf = (list: Order[]) => {
    const map = new Map<number, number>();
    for (const o of list)
      for (const it of o.items) {
        const g = it.guest ?? 0;
        map.set(g, (map.get(g) ?? 0) + Number(it.unit_price) * it.quantity);
      }
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  };
  const guestBreakdown = breakdownOf(selOrders);
  const hasGuests = guestBreakdown.some(([g]) => g !== 0);

  async function saveComment(order: Order) {
    await patch(`/orders/${order.id}/comment/`, { comment: commentText.trim() });
    setCommentEdit(null);
    await reload();
  }

  // закрыть один заказ (не весь стол), зафиксировав способ оплаты
  async function closeOrder(order: Order, method: PayMethod) {
    setBusyClose(order.id);
    try {
      await post(`/orders/${order.id}/close/`, { pay_method: method });
      setPayFor(null);
      await reload();
    } finally {
      setBusyClose(null);
    }
  }

  // отправить заказ на кассу-терминал → «к оплате»
  async function payTerminal(order: Order) {
    setBusyClose(order.id);
    try {
      await post(`/orders/${order.id}/pay_terminal/`, { method: "card" });
      setPayFor(null);
      await reload();
    } finally {
      setBusyClose(null);
    }
  }

  // применить результат оплаты с терминала (в дев-режиме — вручную; в бою придёт вебхуком)
  async function payResult(order: Order, result: "success" | "cancel") {
    setBusyClose(order.id);
    try {
      await post(`/orders/${order.id}/pay_result/`, {
        result,
        fiscal_receipt: result === "success" ? `demo-${order.id}` : "",
      });
      await reload();
    } finally {
      setBusyClose(null);
    }
  }

  async function closeTable(table: string, method: PayMethod) {
    setClosing(true);
    try {
      await post("/orders/close_table/", { table, pay_method: method });
      setPayFor(null);
      setSelected(null);
      await reload();
    } finally {
      setClosing(false);
    }
  }

  // официант переносит заказ на другой стол (гость пересел)
  async function moveOrder(order: Order, table: string) {
    setBusyMove(order.id);
    try {
      await patch(`/orders/${order.id}/move/`, { table });
      setMoveFor(null);
      setSelected(table);
      await reload();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось перенести заказ", "bad");
    } finally {
      setBusyMove(null);
    }
  }

  // перенос только выбранных позиций: уходят в открытый заказ целевого стола
  // (или в новый), остальное остаётся на месте
  async function moveItems(order: Order, table: string) {
    setBusyMove(order.id);
    try {
      await post(`/orders/${order.id}/move_items/`, { table, item_ids: [...moveSel] });
      setMoveFor(null);
      setMoveSel(new Set());
      setSelected(table);
      await reload();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось перенести позиции", "bad");
    } finally {
      setBusyMove(null);
    }
  }

  const toggleMoveSel = (id: number) =>
    setMoveSel((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  // официант подтверждает заявку клиента, назначая стол
  async function confirmRequest(order: Order, table: string) {
    setBusyReq(order.id);
    try {
      await patch(`/orders/${order.id}/confirm/`, { table });
      await Promise.all([reloadRequests(), reload()]);
    } finally {
      setBusyReq(null);
    }
  }

  return (
    <>
      <div className="between">
        <h1 className="h1">Столы</h1>
        <div className="wrap">
          <button className={"btn sm" + (view === "open" ? "" : " ghost")} onClick={() => setView("open")}>Открытые</button>
          <button className={"btn sm" + (view === "closed" ? "" : " ghost")} onClick={() => { setView("closed"); setSelected(null); }}>Закрытые</button>
        </div>
      </div>

      {view === "closed" ? (
        <>
          <p className="muted subtitle">Закрытые счета за сегодня</p>
          <div className="stack loose mt-4">
            {closed.length === 0 ? (
              <p className="muted center mt-5">Сегодня закрытых счетов нет</p>
            ) : (
              closed.map((o) => {
                const open = openClosed.has(o.id);
                const bd = breakdownOf([o]);
                return (
                  <div className="card hover" key={o.id} style={{ cursor: "pointer" }} onClick={() => toggleClosed(o.id)}>
                    <div className="between">
                      <strong>Стол {o.table || "—"} <span className="muted" style={{ fontWeight: 400 }}>· №{o.id}</span></strong>
                      <span className="inline tight">
                        <span className="badge"><Icon name={o.pay_method === "card" ? "card" : "cash"} size={12} /> {o.pay_method_display}</span>
                        <span className="num">{Number(o.total).toLocaleString("ru")} ₽</span>
                      </span>
                    </div>
                    <div className="between mt-1">
                      <span className="muted sm">
                        закрыт {o.closed_at ? fmtClock(o.closed_at) : "—"} · {o.items.length} поз.
                        {o.fiscal_receipt ? ` · чек ${o.fiscal_receipt}` : ""}
                      </span>
                      <span className="muted sm">{open ? "скрыть" : "позиции"}</span>
                    </div>

                    {open && (
                      <div className="rule-top mt-3">
                        <ul className="stack tight list">
                          {o.items.map((it) => (
                            <li key={it.id} className="between">
                              <span>
                                {it.product_name}
                                {it.guest ? <span className="badge open mini ml-2">Гость {it.guest}</span> : null}
                              </span>
                              <span className="num muted">× {it.quantity} · {Number(it.subtotal).toLocaleString("ru")} ₽</span>
                            </li>
                          ))}
                        </ul>
                        {bd.some(([g]) => g !== 0) && (
                          <div className="stack tight mt-2">
                            <div className="muted sm">По гостям:</div>
                            {bd.map(([g, sum]) => (
                              <div className="between" key={g}>
                                <span>{g === 0 ? "Общий" : `Гость ${g}`}</span>
                                <span className="num">{sum.toLocaleString("ru")} ₽</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </>
      ) : (
      <>
      <p className="muted subtitle">Выберите стол, чтобы создать заказ или закрыть счёт</p>

      {serveList.length > 0 && (
        <div className="mt-4">
          <div className="between">
            <strong className="title">К подаче</strong>
            <span className="chip"><Icon name="check" size={15} /> {serveList.length}</span>
          </div>
          <div className="grid cards mt-3">
            {serveList.map(({ order: o, station }) => {
              const its = o.items.filter((it) => it.station === station);
              const key = `${o.id}:${station}`;
              return (
                <div className="card serve-card" key={key}>
                  <div className="between">
                    <strong>
                      Стол {o.table || "—"}
                      <span className="muted" style={{ fontWeight: 400 }}> · №{o.id}</span>
                    </strong>
                    <span className={"badge " + (station === "kitchen" ? "" : "open")}>
                      {station === "kitchen" ? "Кухня" : "Бар"}
                    </span>
                  </div>
                  {o.customer_name && (
                    <div className="muted sm mt-1">{o.customer_name}</div>
                  )}
                  <ul className="stack tight list my-2">
                    {its.map((it) => (
                      <li key={it.id} className="between">
                        <span>{it.product_name}</span>
                        <span className="num muted">× {it.quantity}</span>
                      </li>
                    ))}
                  </ul>
                  <button
                    className="btn sm block"
                    disabled={busyServe === key}
                    onClick={() => serveStation(o, station)}
                  >
                    <Icon name="check" size={15} /> Подал · стол {o.table || "—"}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {requests.length > 0 && (
        <div className="mt-4">
          <div className="between">
            <strong className="title">Заявки клиентов</strong>
            <span className="chip"><Icon name="spark" size={15} /> {requests.length}</span>
          </div>
          <div className="grid cards mt-3">
            {requests.map((o) => (
              <div className={"card request-card" + (reqHighlight.has(o.id) ? " new-order" : "")} key={o.id}>
                <div className="between">
                  <strong>
                    {o.customer_name || "Клиент"}
                    {o.table && <span className="badge open ml-2">Стол {o.table}</span>}
                  </strong>
                  <span className="num">{Number(o.total).toLocaleString("ru")} ₽</span>
                </div>
                <div className="muted sm mt-1">
                  <Icon name="spark" size={12} /> {fmtDuration(minutesBetween(o.created_at))} · {o.items.length} поз.
                </div>
                <ul className="stack tight list my-2">
                  {o.items.map((it) => (
                    <li key={it.id} className="between">
                      <span>{it.product_name}</span>
                      <span className="num muted">× {it.quantity}</span>
                    </li>
                  ))}
                </ul>
                {o.table ? (
                  <button
                    className="btn sm block"
                    disabled={busyReq === o.id}
                    onClick={() => confirmRequest(o, o.table)}
                  >
                    <Icon name="check" size={15} /> Принять · стол {o.table}
                  </button>
                ) : (
                  <>
                    <div className="muted sm">Принять на стол:</div>
                    <div className="scroll-x mt-2">
                      {tables.map((t) => (
                        <button key={t} className="navlink" disabled={busyReq === o.id} onClick={() => confirmRequest(o, t)}>{t}</button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid tiles mt-4">
        {tables.map((t) => {
          const os = byTable[t] ?? [];
          const occupied = os.length > 0;
          const ready = occupied && os.every((o) => o.is_ready);
          const total = os.reduce((s, o) => s + Number(o.total), 0);
          // с открытия самого раннего заказа на столе
          const openedAt = occupied
            ? os.reduce((min, o) => (o.created_at < min ? o.created_at : min), os[0].created_at)
            : null;
          return (
            <button
              key={t}
              className={"card hover table-tile" + (selected === t ? " sel" : "") + (occupied ? (ready ? " ready" : " busy") : " free")}
              onClick={() => setSelected(t)}
            >
              <strong>{t}</strong>
              <span className="state">
                {occupied ? (ready ? "готов" : "готовится") : "свободен"}
              </span>
              {occupied && (
                <span className="muted">
                  <Icon name="spark" size={11} /> {fmtDuration(minutesBetween(openedAt!))}
                </span>
              )}
              {occupied && <span className="num">{total.toLocaleString("ru")} ₽</span>}
            </button>
          );
        })}
      </div>

      {selected && (
        <Modal
          onClose={() => setSelected(null)}
          head={
            <>
              <button className="btn sm ghost" onClick={() => setSelected(null)}>
                <span className="rot-90"><Icon name="arrowDown" size={16} /></span>
                Все столы
              </button>
              <h2>Стол {selected}</h2>
            </>
          }
        >

          {selOrders.length === 0 ? (
            <p className="muted my-3">Стол свободен — заказов нет.</p>
          ) : (
            <div className="stack loose my-3">
              {selOrders.map((o) => (
                <div key={o.id} className="order-card">
                  <div className="between">
                    <strong>
                      №{o.id}
                      {o.customer_name && <span className="muted" style={{ fontWeight: 400 }}> · {o.customer_name}</span>}
                      {" · "}<span className="num">{Number(o.total).toLocaleString("ru")}</span> ₽
                    </strong>
                    {o.status === "awaiting" ? (
                      <span className="badge pending">к оплате</span>
                    ) : (
                      <span className={"badge " + (o.is_ready ? "ready" : "preparing")}>
                        {o.is_ready ? "готов" : "готовится"}
                      </span>
                    )}
                  </div>
                  <div className="muted sm mt-1">
                    <Icon name="spark" size={12} /> {fmtDuration(minutesBetween(o.created_at))} · с открытия
                  </div>

                  {/* статусы станций */}
                  {(o.has_food || o.has_drinks) && (
                    <div className="wrap tight mt-2">
                      {o.has_food && (
                        <span className={"badge " + STATUS_CLASS[o.food_status]}>Кухня: {STATUS_LABEL[o.food_status]}</span>
                      )}
                      {o.has_drinks && (
                        <span className={"badge " + STATUS_CLASS[o.drinks_status]}>Бар: {STATUS_LABEL[o.drinks_status]}</span>
                      )}
                    </div>
                  )}

                  <ul className="stack tight list mt-3">
                    {o.items.map((it) => (
                      <li
                        key={it.id}
                        className={
                          "between" +
                          ((it.station === "kitchen" ? o.food_served : o.drinks_served) ? " item-served" : "")
                        }
                      >
                        <span>
                          {it.product_name}
                          <span className="station-tag">{it.station === "kitchen" ? "кухня" : "бар"}</span>
                          <button
                            className={"badge guest-chip ml-2" + (it.guest ? " open" : "")}
                            disabled={busyGuest === it.id}
                            title="Тап — сменить гостя"
                            onClick={() => cycleGuest(o, it)}
                          >
                            {it.guest ? `Гость ${it.guest}` : "общий"}
                          </button>
                          {/* статус позиции у станции — объясняет, почему её нельзя убрать */}
                          {it.status !== "new" && (
                            <span className={"badge mini ml-2 " + STATUS_CLASS[it.status]}>
                              {STATUS_LABEL[it.status]}
                            </span>
                          )}
                        </span>
                        {confirmId === it.id ? (
                          <span className="inline tight">
                            <span className="muted sm">Убрать?</span>
                            <button
                              className="icon-btn danger"
                              title="Да, убрать"
                              disabled={busyItem === it.id}
                              onClick={() => removeItem(o, it)}
                            >
                              <Icon name="check" size={16} />
                            </button>
                            <button
                              className="icon-btn"
                              title="Отмена"
                              onClick={() => setConfirmId(null)}
                            >
                              <Icon name="close" size={16} />
                            </button>
                          </span>
                        ) : codeFor === it.id ? (
                          // позиция уже в работе — убрать можно, только назвав код из настроек
                          <span className="inline tight">
                            <input
                              className="input"
                              style={{ width: 80, minHeight: "auto", padding: "8px 10px" }}
                              value={codeText}
                              onChange={(e) => setCodeText(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && codeText.trim()) removeItem(o, it, codeText.trim());
                              }}
                              placeholder="код"
                              maxLength={20}
                              autoFocus
                            />
                            <button
                              className="icon-btn danger"
                              title="Убрать по коду"
                              disabled={busyItem === it.id || !codeText.trim()}
                              onClick={() => removeItem(o, it, codeText.trim())}
                            >
                              <Icon name="check" size={16} />
                            </button>
                            <button
                              className="icon-btn"
                              title="Отмена"
                              onClick={() => { setCodeFor(null); setCodeText(""); }}
                            >
                              <Icon name="close" size={16} />
                            </button>
                          </span>
                        ) : it.status !== "new" ? (
                          <button
                            className="icon-btn"
                            title="Кухня/бар уже готовят — убрать можно только по коду"
                            onClick={() => { setCodeFor(it.id); setCodeText(""); }}
                          >
                            <Icon name="lock" size={16} />
                          </button>
                        ) : (
                          <Stepper
                            value={"× " + it.quantity}
                            width={112}
                            disabled={busyItem === it.id}
                            decDanger={it.quantity === 1}
                            decIcon={it.quantity === 1 ? "trash" : "minus"}
                            ariaDec={it.quantity === 1 ? "Убрать позицию" : "На одну меньше"}
                            ariaInc="Ещё одну"
                            onDec={() => (it.quantity > 1 ? changeQty(o, it, -1) : setConfirmId(it.id))}
                            onInc={() => changeQty(o, it, +1)}
                          />
                        )}
                      </li>
                    ))}
                  </ul>

                  {/* комментарий: заметка или инлайн-редактор */}
                  {commentEdit === o.id ? (
                    <div className="wrap mt-3">
                      <input
                        className="input grow"
                        value={commentText}
                        onChange={(e) => setCommentText(e.target.value)}
                        placeholder="без лука, аллергия, стол у окна…"
                        maxLength={300}
                        autoFocus
                      />
                      <button className="icon-btn" onClick={() => saveComment(o)} aria-label="Сохранить"><Icon name="check" size={16} /></button>
                      <button className="icon-btn" onClick={() => setCommentEdit(null)} aria-label="Отмена"><Icon name="close" size={16} /></button>
                    </div>
                  ) : o.comment ? (
                    <button
                      className="order-note mt-3"
                      onClick={() => { setCommentEdit(o.id); setCommentText(o.comment); }}
                      title="Изменить комментарий"
                    >
                      <Icon name="edit" size={14} /> {o.comment}
                    </button>
                  ) : null}

                  {/* инлайн-перенос на другой стол: весь заказ или отмеченные позиции */}
                  {moveFor === o.id && (
                    <div className="mt-3">
                      <div className="between">
                        <span className="muted sm">
                          {moveSel.size
                            ? `Перенести ${moveSel.size} поз. на стол:`
                            : "Перенести весь заказ на стол:"}
                        </span>
                        <button className="icon-btn" onClick={() => { setMoveFor(null); setMoveSel(new Set()); }} aria-label="Отмена"><Icon name="close" size={16} /></button>
                      </div>
                      {o.items.length > 1 && (
                        <>
                          <div className="wrap tight mt-2">
                            {o.items.map((it) => (
                              <button
                                key={it.id}
                                className={"badge guest-chip" + (moveSel.has(it.id) ? " open" : "")}
                                onClick={() => toggleMoveSel(it.id)}
                              >
                                {it.product_name} × {it.quantity}
                              </button>
                            ))}
                          </div>
                          <div className="muted sm mt-1">
                            Отметьте позиции, чтобы перенести только их
                          </div>
                        </>
                      )}
                      <div className="scroll-x mt-2">
                        {tables.filter((t) => t !== o.table).map((t) => (
                          <button
                            key={t}
                            className="navlink"
                            disabled={busyMove === o.id}
                            onClick={() => (moveSel.size ? moveItems(o, t) : moveOrder(o, t))}
                          >
                            {t}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* второстепенные действия — компактный ряд */}
                  {commentEdit !== o.id && moveFor !== o.id && o.status !== "awaiting" && (
                    <div className="wrap mt-3">
                      <button className="btn sm ghost" onClick={() => setAddFor(o)}>
                        <Icon name="plus" size={15} /> Позиция
                      </button>
                      {!o.comment && (
                        <button className="btn sm ghost" onClick={() => { setCommentEdit(o.id); setCommentText(""); }}>
                          <Icon name="edit" size={15} /> Комментарий
                        </button>
                      )}
                      <button className="btn sm ghost" disabled={busyMove === o.id} onClick={() => { setMoveFor(o.id); setMoveSel(new Set()); }}>
                        <Icon name="store" size={15} /> Перенести
                      </button>
                    </div>
                  )}

                  {/* оплата: ожидание терминала / выбор способа / кнопка закрытия */}
                  {o.status === "awaiting" ? (
                    <div className="rule-top mt-3">
                      <div className="inline">
                        <span className="spin" style={{ display: "inline-flex", color: "var(--brand)" }}>
                          <Icon name="spark" size={16} />
                        </span>
                        <strong>Ожидаем оплату на терминале…</strong>
                      </div>
                      <div className="muted sm mt-1">
                        Сумма {Number(o.total).toLocaleString("ru")} ₽ · оплата на кассе
                      </div>
                      {DEV && (
                        <div className="grid cols-2 mt-2">
                          <button className="btn" disabled={busyClose === o.id} onClick={() => payResult(o, "success")}>
                            <Icon name="check" size={16} /> Оплата прошла
                          </button>
                          <button className="btn ghost" disabled={busyClose === o.id} onClick={() => payResult(o, "cancel")}>
                            <Icon name="close" size={16} /> Отмена
                          </button>
                        </div>
                      )}
                    </div>
                  ) : payFor === o.id ? (
                    <div className="rule-top mt-3">
                      <div className="muted sm center">Оплата · {Number(o.total).toLocaleString("ru")} ₽</div>
                      <div className="grid cols-2 mt-2">
                        <button className="btn" disabled={busyClose === o.id} onClick={() => closeOrder(o, "cash")}>
                          <Icon name="cash" size={17} /> Наличными
                        </button>
                        <button className="btn" disabled={busyClose === o.id} onClick={() => closeOrder(o, "card")}>
                          <Icon name="card" size={17} /> Картой
                        </button>
                      </div>
                      {/* «На терминал» скрыт до реальной интеграции провайдера:
                          в проде дев-кнопки завершения оплаты нет, заказ завис бы в «к оплате» */}
                      {DEV && (
                        <button className="btn block mt-2" disabled={busyClose === o.id} onClick={() => payTerminal(o)}>
                          <Icon name="card" size={17} /> На терминал
                        </button>
                      )}
                      <button className="btn sm ghost block mt-2" onClick={() => setPayFor(null)}>Отмена</button>
                    </div>
                  ) : (
                    <button className="btn block mt-3" onClick={() => setPayFor(o.id)}>
                      <Icon name="check" size={17} /> Закрыть заказ · {Number(o.total).toLocaleString("ru")} ₽
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {selOrders.length > 0 && (
            <div className="rule-top mt-3">
              <div className="between">
                <span className="muted sm">Разбить по гостям</span>
                <Stepper
                  value={splitN}
                  width={108}
                  onDec={() => setSplitN((n) => Math.max(2, n - 1))}
                  onInc={() => setSplitN((n) => Math.min(12, n + 1))}
                />
              </div>
              <div className="muted sm mt-2">
                Тапайте позицию, чтобы назначить гостя (Общий → 1 … {splitN})
              </div>
              {hasGuests && (
                <div className="stack tight mt-3">
                  {guestBreakdown.map(([g, sum]) => (
                    <div className="between" key={g}>
                      <span>{g === 0 ? "Общий" : `Гость ${g}`}</span>
                      <span className="num">{sum.toLocaleString("ru")} ₽</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {payFor === "table" ? (
            <div className="rule-top mt-4">
              <div className="muted sm center">Оплата всего стола · {selTotal.toLocaleString("ru")} ₽</div>
              <div className="grid cols-2 mt-2">
                <button className="btn" disabled={closing} onClick={() => closeTable(selected, "cash")}>
                  <Icon name="cash" size={17} /> Наличными
                </button>
                <button className="btn" disabled={closing} onClick={() => closeTable(selected, "card")}>
                  <Icon name="card" size={17} /> Картой
                </button>
              </div>
              <button className="btn sm ghost block mt-2" onClick={() => setPayFor(null)}>Отмена</button>
            </div>
          ) : (
            <div className="wrap mt-4">
              <button className="btn sm" onClick={() => setComposeFor(selected)}>
                <Icon name="plus" size={16} /> Новый заказ
              </button>
              {selOrders.length > 1 && (
                <button
                  className="btn sm ghost"
                  onClick={() => setPayFor("table")}
                  disabled={closing}
                >
                  <Icon name="check" size={16} /> Закрыть весь стол{selTotal ? ` · ${selTotal.toLocaleString("ru")} ₽` : ""}
                </button>
              )}
            </div>
          )}
        </Modal>
      )}
      </>
      )}
    </>
  );
}
