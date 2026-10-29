import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get, patch, ApiError } from "../../api";
import Icon, { type IconName } from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite, useUpdateSite } from "../../site";
import type { DashPeriod, DashTotals, Dashboard as Data, Site } from "../../types";

/** «Сводка» — первое, что видит владелец.

    Отвечает на три вопроса: сколько заработали, лучше ли это обычного и
    что надо сделать прямо сейчас. Все настройки уехали в «Настройки»:
    их трогают раз в месяц, а сюда смотрят каждый день. Считает сервер
    (orders/dashboard.py) — страница не тянет к себе историю заказов.
*/

const PERIODS: { key: DashPeriod; label: string; vs: string }[] = [
  { key: "today", label: "Сегодня", vs: "к этому дню неделю назад" },
  { key: "week", label: "Неделя", vs: "к прошлой неделе" },
  { key: "month", label: "Месяц", vs: "к прошлому месяцу" },
];

const PERIOD_KEY = "dash_period";

// Копейки в сводке не нужны и только удлиняют семизначные суммы
const rub = (v: string | number) => Math.round(Number(v)).toLocaleString("ru") + " ₽";

function loadPeriod(): DashPeriod {
  try {
    const v = localStorage.getItem(PERIOD_KEY);
    if (v === "today" || v === "week" || v === "month") return v;
  } catch {
    /* приватный режим — просто сегодня */
  }
  return "today";
}

/** Изменение к прошлому окну. Без базы сравнивать не с чем — молчим. */
function Delta({ cur, prev, vs }: { cur: number; prev: number; vs: string }) {
  if (!prev) return <span className="dash-delta muted">{cur ? "не с чем сравнить" : " "}</span>;
  const pct = Math.round(((cur - prev) / prev) * 100);
  const cls = pct > 0 ? " up" : pct < 0 ? " down" : "";
  return (
    <span className={"dash-delta" + cls} title={vs}>
      {pct > 0 ? "↑" : pct < 0 ? "↓" : "="} {Math.abs(pct)}% <span className="muted">{vs}</span>
    </span>
  );
}

/** Выключатель приёма заказов — под рукой: за ним бегут в час пик.
    Причину перерыва пишут в «Настройках», здесь только вкл/выкл. */
function PauseSwitch({ onChange }: { onChange: () => void }) {
  const site = useSite();
  const updateSite = useUpdateSite();
  const notify = useToast();
  const [saving, setSaving] = useState(false);
  // состояние держим в общем контексте сайта: «Настройки» → «Заказы с
  // сайта» должны показать то же самое без перезагрузки
  const on = site?.ordering_paused ?? false;

  async function toggle() {
    setSaving(true);
    try {
      updateSite(await patch<Site>("/site/", { ordering_paused: !on }));
      onChange();
      notify(!on ? "Заказы с сайта остановлены" : "Заказы с сайта снова принимаются", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  return (
    <button
      className={"dash-pause" + (on ? " on" : "")}
      onClick={toggle}
      disabled={saving}
      role="switch"
      aria-checked={!on}
    >
      <span className="dash-switch" aria-hidden="true" />
      {on ? "Заказы на паузе" : "Заказы принимаются"}
    </button>
  );
}

function Channels({ t }: { t: DashTotals }) {
  const parts: { key: string; label: string; icon: IconName; v: number }[] = [
    { key: "cash", label: "Наличные", icon: "cash", v: Number(t.cash) },
    { key: "card", label: "Карта на месте", icon: "card", v: Number(t.card) },
    { key: "online", label: "Онлайн", icon: "laptop", v: Number(t.online) },
  ];
  const sum = parts.reduce((s, p) => s + p.v, 0);
  return (
    <div className="card">
      <div className="between">
        <strong className="title">Как платили</strong>
        {Number(t.bonuses) > 0 && (
          <span className="muted sm">бонусами {rub(t.bonuses)}</span>
        )}
      </div>
      <div className="dash-stack mt-3" aria-hidden="true">
        {sum > 0 &&
          parts.map((p) =>
            p.v > 0 ? (
              <span key={p.key} className={"seg ch-" + p.key} style={{ flexGrow: p.v }} />
            ) : null
          )}
      </div>
      <div className="dash-legend mt-3">
        {parts.map((p) => (
          <div key={p.key} className="dash-legend-row">
            <span className={"dot ch-" + p.key} />
            <span className="muted">{p.label}</span>
            <strong className="num">{rub(p.v)}</strong>
            <span className="muted sm num">{sum ? Math.round((p.v / sum) * 100) : 0}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Chart({ data }: { data: Data }) {
  const hourly = data.period === "today";
  const vals = data.series.map((p) => Math.max(Number(p.value ?? 0), Number(p.prev)));
  const max = Math.max(...vals, 1);
  // в часовом графике ночные часы пустые всегда — обрезаем их по краям
  let from = 0;
  let to = data.series.length;
  if (hourly) {
    const busy = data.series
      .map((p, i) => (Number(p.value ?? 0) || Number(p.prev) ? i : -1))
      .filter((i) => i >= 0);
    from = busy.length ? Math.max(0, Math.min(busy[0], 8)) : 8;
    to = busy.length ? Math.min(24, Math.max(busy[busy.length - 1] + 1, 22)) : 22;
  }
  const points = data.series.slice(from, to);
  const label = (at: string, i: number) => {
    const d = new Date(at);
    if (hourly) return (from + i) % 3 === 0 ? String(d.getHours()) : "";
    if (data.period === "week") return d.toLocaleDateString("ru", { weekday: "short" });
    return d.getDate() === 1 || d.getDate() % 5 === 0 ? String(d.getDate()) : "";
  };
  const tip = (at: string) => {
    const d = new Date(at);
    return hourly
      ? `${d.getHours()}:00`
      : d.toLocaleDateString("ru", { weekday: "short", day: "numeric", month: "short" });
  };

  return (
    <div className="card">
      <div className="between">
        <strong className="title">{hourly ? "По часам" : "По дням"}</strong>
        <span className="dash-key">
          <span><i className="cur" /> сейчас</span>
          <span><i className="prev" /> {hourly ? "неделю назад" : "прошлый период"}</span>
        </span>
      </div>
      <div className="dash-chart mt-4">
        {points.map((p, i) => (
          <div
            key={p.at}
            className="dash-col"
            title={`${tip(p.at)}: ${p.value === null ? "ещё впереди" : rub(p.value)} · было ${rub(p.prev)}`}
          >
            <div className="dash-bars">
              <span className="bar prev" style={{ height: `${(Number(p.prev) / max) * 100}%` }} />
              {p.value !== null && (
                <span className="bar cur" style={{ height: `${(Number(p.value) / max) * 100}%` }} />
              )}
            </div>
            <span className="dash-tick">{label(p.at, i)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Top({ data }: { data: Data }) {
  const max = Math.max(...data.top.map((t) => Number(t.sum)), 1);
  return (
    <div className="card">
      <strong className="title">Лучше всего продаётся</strong>
      {data.top.length === 0 ? (
        <p className="muted mt-3 m-0">Продаж за этот период пока нет.</p>
      ) : (
        <ol className="dash-top mt-3">
          {data.top.map((t, i) => (
            <li key={t.name + t.variant}>
              <span className="dash-rank">{i + 1}</span>
              <div className="dash-top-body">
                <div className="between">
                  <span className="dash-top-name">
                    {t.name}
                    {t.variant && <span className="muted"> · {t.variant}</span>}
                  </span>
                  <strong className="num">{rub(t.sum)}</strong>
                </div>
                <div className="dash-top-bar">
                  <span style={{ width: `${(Number(t.sum) / max) * 100}%` }} />
                </div>
                <span className="muted sm">{t.qty} шт.</span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

const ATTENTION_ICON: Record<string, IconName> = {
  license: "lock",
  paused: "lock",
  waiting: "receipt",
  stock: "box",
  refunds: "minus",
};

export default function Dashboard() {
  const [period, setPeriod] = useState<DashPeriod>(loadPeriod);
  const [data, setData] = useState<Data | null>(null);
  const [failed, setFailed] = useState(false);
  // после переключения паузы блок «внимание» должен обновиться сразу
  const [reload, setReload] = useState(0);

  useEffect(() => {
    try {
      localStorage.setItem(PERIOD_KEY, period);
    } catch {
      /* не запомнили — не страшно */
    }
    let alive = true;
    const load = () =>
      get<Data>(`/dashboard/?period=${period}`)
        .then((d) => {
          if (alive) {
            setData(d);
            setFailed(false);
          }
        })
        .catch(() => alive && setFailed(true));
    load();
    // сводку держат открытой весь день — обновляем раз в минуту
    const id = window.setInterval(load, 60_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [period, reload]);

  const vs = PERIODS.find((p) => p.key === period)!.vs;
  // пока грузится новый период, старые цифры не выдаём за новые
  const shown = data && data.period === period ? data : null;
  const cur = shown?.current;
  const prev = shown?.previous;

  return (
    <>
      <div className="dash-head">
        <h1 className="h1 m-0">Сводка</h1>
        <div className="dash-head-actions">
          <Link to="/orders" className="dash-live">
            <span className="pulse" aria-hidden="true" />
            В работе: <strong className="num">{data?.in_work ?? "…"}</strong>
          </Link>
          <PauseSwitch onChange={() => setReload((n) => n + 1)} />
        </div>
      </div>

      <div className="dash-periods" role="tablist">
        {PERIODS.map((p) => (
          <button
            key={p.key}
            role="tab"
            aria-selected={period === p.key}
            className={"navlink" + (period === p.key ? " active" : "")}
            onClick={() => setPeriod(p.key)}
          >
            {p.label}
          </button>
        ))}
      </div>

      {failed && !data && (
        <div className="card mt-4">
          <p className="muted m-0">Не удалось загрузить сводку. Обновим через минуту.</p>
        </div>
      )}

      {data && data.attention.length > 0 && (
        <div className="dash-attention mt-4">
          {data.attention.map((a) => (
            <Link key={a.key} to={a.to} className={"dash-alert " + a.kind}>
              <span className="tx-icon">
                <Icon name={ATTENTION_ICON[a.key] ?? "alert"} size={18} />
              </span>
              <span className="dash-alert-body">
                <strong>{a.title}</strong>
                <span className="muted sm">{a.text}</span>
              </span>
              <Icon name="arrowRight" size={18} />
            </Link>
          ))}
        </div>
      )}

      <div className={"dash-kpis mt-4" + (shown ? "" : " loading")}>
        <div className="card dash-kpi main">
          <span className="muted">Выручка</span>
          <div className="dash-kpi-value">{cur ? rub(cur.revenue) : "…"}</div>
          {cur && prev && <Delta cur={Number(cur.revenue)} prev={Number(prev.revenue)} vs={vs} />}
          {cur && Number(cur.refunds) > 0 && (
            <span className="muted sm">с учётом возвратов на {rub(cur.refunds)}</span>
          )}
        </div>
        <div className="card dash-kpi">
          <span className="muted">Чеков</span>
          <div className="dash-kpi-value">{cur ? cur.checks.toLocaleString("ru") : "…"}</div>
          {cur && prev && <Delta cur={cur.checks} prev={prev.checks} vs={vs} />}
        </div>
        <div className="card dash-kpi">
          <span className="muted">Средний чек</span>
          <div className="dash-kpi-value">{cur ? rub(cur.avg_check) : "…"}</div>
          {cur && prev && (
            <Delta cur={Number(cur.avg_check)} prev={Number(prev.avg_check)} vs={vs} />
          )}
        </div>
      </div>

      {shown && cur && (
        <div className="dash-grid mt-4">
          <div className="dash-wide">
            <Chart data={shown} />
          </div>
          <Channels t={cur} />
          <Top data={shown} />
        </div>
      )}
    </>
  );
}
