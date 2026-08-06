import { useEffect, useMemo, useRef, useState } from "react";
import { get, patch, post } from "../../api";
import type { Order, OrderItem, Station, StationStatus, Table } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";
import { playChime } from "../../sound";
import { fmtClock, fmtDuration, minutesBetween } from "../../time";
import Compose from "./Compose";

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

export default function Waiter() {
  const { orders, reload } = useLiveOrders("/orders/?status=open", { sound: false });
  // заявки от клиентов (без стола) — со звуком, чтобы официант заметил
  const { orders: requests, highlight: reqHighlight, reload: reloadRequests } =
    useLiveOrders("/orders/?status=requested", { sound: true });
  const [selected, setSelected] = useState<string | null>(null);
  const [composeFor, setComposeFor] = useState<string | null>(null);
  const [addFor, setAddFor] = useState<Order | null>(null);
  const [closing, setClosing] = useState(false);
  const [busyItem, setBusyItem] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
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
  const [busyMove, setBusyMove] = useState<number | null>(null);
  const [busyClose, setBusyClose] = useState<number | null>(null);
  const [busyServe, setBusyServe] = useState<string | null>(null);
  const seenServe = useRef<Set<string> | null>(null);

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
  async function removeItem(order: Order, item: OrderItem) {
    setBusyItem(item.id);
    try {
      await post(`/orders/${order.id}/remove_item/`, { item_id: item.id });
      setConfirmId(null);
      await reload();
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
    for (const o of orders) {
      if (o.has_food && o.food_status === "ready" && !o.food_served)
        rows.push({ order: o, station: "kitchen" });
      if (o.has_drinks && o.drinks_status === "ready" && !o.drinks_served)
        rows.push({ order: o, station: "bar" });
    }
    return rows;
  }, [orders]);

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
      await reload();
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

  // закрыть один заказ (не весь стол)
  async function closeOrder(order: Order) {
    setBusyClose(order.id);
    try {
      await post(`/orders/${order.id}/close/`, {});
      await reload();
    } finally {
      setBusyClose(null);
    }
  }

  async function closeTable(table: string) {
    setClosing(true);
    try {
      await post("/orders/close_table/", { table });
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
    } finally {
      setBusyMove(null);
    }
  }

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
          <p className="muted" style={{ marginTop: 4 }}>Закрытые счета за сегодня</p>
          <div className="stack" style={{ gap: 10, marginTop: 16 }}>
            {closed.length === 0 ? (
              <p className="muted" style={{ textAlign: "center", marginTop: 24 }}>Сегодня закрытых счетов нет</p>
            ) : (
              closed.map((o) => {
                const open = openClosed.has(o.id);
                const bd = breakdownOf([o]);
                return (
                  <div className="card hover" key={o.id} style={{ cursor: "pointer" }} onClick={() => toggleClosed(o.id)}>
                    <div className="between">
                      <strong>Стол {o.table || "—"} <span className="muted" style={{ fontWeight: 400 }}>· №{o.id}</span></strong>
                      <span className="num">{Number(o.total).toLocaleString("ru")} ₽</span>
                    </div>
                    <div className="between" style={{ marginTop: 2 }}>
                      <span className="muted" style={{ fontSize: 12 }}>
                        закрыт {o.closed_at ? fmtClock(o.closed_at) : "—"} · {o.items.length} поз.
                      </span>
                      <span className="muted" style={{ fontSize: 12 }}>{open ? "скрыть" : "позиции"}</span>
                    </div>

                    {open && (
                      <div style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                        <ul className="stack" style={{ gap: 4, listStyle: "none", padding: 0, margin: 0 }}>
                          {o.items.map((it) => (
                            <li key={it.id} className="between">
                              <span>
                                {it.product_name}
                                {it.guest ? <span className="badge open" style={{ marginLeft: 6, padding: "1px 7px", fontSize: 11 }}>Гость {it.guest}</span> : null}
                              </span>
                              <span className="num muted">× {it.quantity} · {Number(it.subtotal).toLocaleString("ru")} ₽</span>
                            </li>
                          ))}
                        </ul>
                        {bd.some(([g]) => g !== 0) && (
                          <div className="stack" style={{ gap: 3, marginTop: 8 }}>
                            <div className="muted" style={{ fontSize: 12 }}>По гостям:</div>
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
      <p className="muted" style={{ marginTop: 4 }}>Выберите стол, чтобы создать заказ или закрыть счёт</p>

      {serveList.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="between">
            <strong style={{ fontFamily: "Fredoka", fontSize: 17 }}>К подаче</strong>
            <span className="chip"><Icon name="check" size={15} /> {serveList.length}</span>
          </div>
          <div className="grid" style={{ marginTop: 10, gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
            {serveList.map(({ order: o, station }) => {
              const its = o.items.filter((it) => it.station === station);
              const key = `${o.id}:${station}`;
              return (
                <div className="card new-order" key={key} style={{ borderColor: "#4a9c6d" }}>
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
                    <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{o.customer_name}</div>
                  )}
                  <ul className="stack" style={{ gap: 3, margin: "8px 0", listStyle: "none", padding: 0 }}>
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
        <div style={{ marginTop: 16 }}>
          <div className="between">
            <strong style={{ fontFamily: "Fredoka", fontSize: 17 }}>Заявки клиентов</strong>
            <span className="chip"><Icon name="spark" size={15} /> {requests.length}</span>
          </div>
          <div className="grid" style={{ marginTop: 10, gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
            {requests.map((o) => (
              <div className={"card" + (reqHighlight.has(o.id) ? " new-order" : "")} key={o.id}>
                <div className="between">
                  <strong>
                    {o.customer_name || "Клиент"}
                    {o.table && <span className="badge open" style={{ marginLeft: 6 }}>Стол {o.table}</span>}
                  </strong>
                  <span className="num">{Number(o.total).toLocaleString("ru")} ₽</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                  <Icon name="spark" size={12} /> {fmtDuration(minutesBetween(o.created_at))} · {o.items.length} поз.
                </div>
                <ul className="stack" style={{ gap: 3, margin: "8px 0", listStyle: "none", padding: 0 }}>
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
                    <div className="muted" style={{ fontSize: 12 }}>Принять на стол:</div>
                    <div className="scroll-x" style={{ marginTop: 6 }}>
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

      <div className="grid" style={{ marginTop: 16, gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }}>
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
              <strong style={{ fontFamily: "Fredoka", fontSize: 22 }}>{t}</strong>
              <span style={{ fontSize: 14.5, fontWeight: 700 }}>
                {occupied ? (ready ? "готов" : "готовится") : "свободен"}
              </span>
              {occupied && (
                <span className="muted" style={{ fontSize: 11.5, display: "inline-flex", alignItems: "center", gap: 3 }}>
                  <Icon name="spark" size={11} /> {fmtDuration(minutesBetween(openedAt!))}
                </span>
              )}
              {occupied && <span className="num" style={{ fontSize: 18, fontWeight: 700 }}>{total.toLocaleString("ru")} ₽</span>}
            </button>
          );
        })}
      </div>

      {selected && (
        <div
          className="table-modal"
          role="dialog"
          aria-modal="true"
          onClick={(e) => { if (e.target === e.currentTarget) setSelected(null); }}
        >
        <div className="table-modal-panel">
          <div className="table-modal-head">
            <button className="btn sm ghost" onClick={() => setSelected(null)}>
              <span style={{ display: "inline-flex", transform: "rotate(90deg)" }}><Icon name="arrowDown" size={16} /></span>
              Все столы
            </button>
            <h2>Стол {selected}</h2>
          </div>
          <div className="table-modal-body">

          {selOrders.length === 0 ? (
            <p className="muted" style={{ margin: "12px 0" }}>Стол свободен — заказов нет.</p>
          ) : (
            <div className="stack" style={{ gap: 12, margin: "12px 0" }}>
              {selOrders.map((o) => (
                <div key={o.id} style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                  <div className="between">
                    <strong>
                      №{o.id}
                      {o.customer_name && <span className="muted" style={{ fontWeight: 400 }}> · {o.customer_name}</span>}
                      {" · "}<span className="num">{o.total}</span> ₽
                    </strong>
                    <span className={"badge " + (o.is_ready ? "ready" : "preparing")}>
                      {o.is_ready ? "готов" : "готовится"}
                    </span>
                  </div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                    <Icon name="spark" size={12} /> {fmtDuration(minutesBetween(o.created_at))} · с открытия
                  </div>
                  <ul className="stack" style={{ gap: 3, margin: "8px 0 0", listStyle: "none", padding: 0 }}>
                    {o.items.map((it) => (
                      <li key={it.id} className="between">
                        <span>
                          {it.product_name}
                          <span className="muted" style={{ fontSize: 12 }}> · {it.station === "kitchen" ? "кухня" : "бар"}</span>
                          <button
                            className={"badge guest-chip" + (it.guest ? " open" : "")}
                            style={{ marginLeft: 6 }}
                            disabled={busyGuest === it.id}
                            title="Тап — сменить гостя"
                            onClick={() => cycleGuest(o, it)}
                          >
                            {it.guest ? `Гость ${it.guest}` : "общий"}
                          </button>
                        </span>
                        {confirmId === it.id ? (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                            <span className="muted" style={{ fontSize: 12.5 }}>Убрать?</span>
                            <button
                              className="icon-btn sm danger"
                              title="Да, убрать"
                              disabled={busyItem === it.id}
                              onClick={() => removeItem(o, it)}
                            >
                              <Icon name="check" size={14} />
                            </button>
                            <button
                              className="icon-btn sm"
                              title="Отмена"
                              onClick={() => setConfirmId(null)}
                            >
                              <span style={{ display: "inline-flex", transform: "rotate(45deg)" }}>
                                <Icon name="plus" size={14} />
                              </span>
                            </button>
                          </span>
                        ) : (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
                            <span className="num muted">× {it.quantity}</span>
                            <button
                              className="icon-btn sm danger"
                              title="Убрать позицию"
                              onClick={() => setConfirmId(it.id)}
                            >
                              <Icon name="minus" size={14} />
                            </button>
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                  <button
                    className="btn sm ghost block"
                    style={{ marginTop: 8 }}
                    onClick={() => setAddFor(o)}
                  >
                    <Icon name="plus" size={15} /> Добавить позицию
                  </button>
                  <div className="wrap" style={{ marginTop: 8 }}>
                    {o.has_food && (
                      <span className={"badge " + STATUS_CLASS[o.food_status]}>Кухня: {STATUS_LABEL[o.food_status]}</span>
                    )}
                    {o.has_drinks && (
                      <span className={"badge " + STATUS_CLASS[o.drinks_status]}>Бар: {STATUS_LABEL[o.drinks_status]}</span>
                    )}
                  </div>

                  {commentEdit === o.id ? (
                    <div className="wrap" style={{ gap: 8, marginTop: 10 }}>
                      <input
                        className="input"
                        style={{ flex: 1, minWidth: 160 }}
                        value={commentText}
                        onChange={(e) => setCommentText(e.target.value)}
                        placeholder="без лука, аллергия, стол у окна…"
                        maxLength={300}
                        autoFocus
                      />
                      <button className="icon-btn" onClick={() => saveComment(o)} aria-label="Сохранить"><Icon name="check" size={16} /></button>
                      <button className="icon-btn" onClick={() => setCommentEdit(null)} aria-label="Отмена"><Icon name="minus" size={16} /></button>
                    </div>
                  ) : o.comment ? (
                    <button
                      className="order-note"
                      onClick={() => { setCommentEdit(o.id); setCommentText(o.comment); }}
                      title="Изменить комментарий"
                    >
                      <Icon name="edit" size={14} /> {o.comment}
                    </button>
                  ) : (
                    <button
                      className="btn sm ghost"
                      style={{ marginTop: 10 }}
                      onClick={() => { setCommentEdit(o.id); setCommentText(""); }}
                    >
                      <Icon name="plus" size={15} /> Комментарий
                    </button>
                  )}

                  {moveFor === o.id ? (
                    <div style={{ marginTop: 10 }}>
                      <div className="between">
                        <span className="muted" style={{ fontSize: 12 }}>Перенести на стол:</span>
                        <button className="icon-btn sm" onClick={() => setMoveFor(null)} aria-label="Отмена"><Icon name="minus" size={14} /></button>
                      </div>
                      <div className="scroll-x" style={{ marginTop: 6 }}>
                        {tables.filter((t) => t !== o.table).map((t) => (
                          <button key={t} className="navlink" disabled={busyMove === o.id} onClick={() => moveOrder(o, t)}>{t}</button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <button
                      className="btn sm ghost"
                      style={{ marginTop: 10, marginLeft: 8 }}
                      disabled={busyMove === o.id}
                      onClick={() => setMoveFor(o.id)}
                    >
                      <Icon name="store" size={15} /> Перенести на другой стол
                    </button>
                  )}

                  <div style={{ marginTop: 10 }}>
                    <button
                      className="btn sm block"
                      disabled={busyClose === o.id}
                      onClick={() => closeOrder(o)}
                    >
                      <Icon name="check" size={16} /> Закрыть заказ · {Number(o.total).toLocaleString("ru")} ₽
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {selOrders.length > 0 && (
            <div style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
              <div className="between">
                <span className="muted" style={{ fontSize: 12.5 }}>Разбить по гостям</span>
                <div className="stepper" style={{ width: 108 }}>
                  <button onClick={() => setSplitN((n) => Math.max(2, n - 1))} aria-label="Меньше"><Icon name="minus" size={14} /></button>
                  <span className="count num">{splitN}</span>
                  <button onClick={() => setSplitN((n) => Math.min(12, n + 1))} aria-label="Больше"><Icon name="plus" size={14} /></button>
                </div>
              </div>
              <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                Тапайте позицию, чтобы назначить гостя (Общий → 1 … {splitN})
              </div>
              {hasGuests && (
                <div className="stack" style={{ gap: 4, marginTop: 10 }}>
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

          <div className="wrap" style={{ marginTop: 14 }}>
            <button className="btn sm" onClick={() => setComposeFor(selected)}>
              <Icon name="plus" size={16} /> Новый заказ
            </button>
            {selOrders.length > 1 && (
              <button
                className="btn sm ghost"
                onClick={() => closeTable(selected)}
                disabled={closing}
              >
                <Icon name="check" size={16} /> Закрыть весь стол{selTotal ? ` · ${selTotal.toLocaleString("ru")} ₽` : ""}
              </button>
            )}
          </div>
          </div>
        </div>
        </div>
      )}
      </>
      )}
    </>
  );
}
