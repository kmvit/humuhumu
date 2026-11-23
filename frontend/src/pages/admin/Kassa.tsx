import EvotorConnection from "./EvotorConnection";
import { type CloudSettings } from "../counter/EvotorCloud";
import { useEffect, useState } from "react";
import { del, get, patch, post, put, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";

/** Раздел «Касса» в панели владельца.

    Касса — это то, куда уходит заказ, когда гость выбирает «Оплатить на
    кассе»: бариста принимает там наличные или карту, касса печатает чек,
    а заказ сам уходит в работу. С онлайн-оплатой не связана — это другой
    договор и другое устройство.

    Ключ, как и ключи банка, сюда не приходит никогда — только «задан».
    Несекретные поля (магазин, ставка НДС) приходят значениями.
*/
type KassaField = {
  key: string;
  label: string;
  hint: string;
  secret: boolean;
  required: boolean;
  choices: { value: string; label: string }[];
  default: string;
};

type KassaKind = { name: string; title: string; fields: KassaField[] };

type State = {
  provider: string;
  ready: boolean;
  filled: Record<string, boolean>;
  values: Record<string, string>;
  kassas: KassaKind[];
  /** Синхронизация терминала через кабинет кассы; null — не настроена. */
  sync: { synced_at: string | null; attempted_at: string | null; error: string } | null;
  /** Последний отказ кассы, после которого заказы на неё не уходили. */
  last_error: { error: string; at: string; order: number | null } | null;
};

const when = (iso: string) =>
  new Date(iso).toLocaleString("ru", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });

const NONE = "none";
const EVOTOR = "evotor_cloud";

async function loadCloudSettings() {
  try {
    return await post<CloudSettings>("/evotor-cloud/admin/settings/", {});
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export default function Kassa() {
  const notify = useToast();
  const [cloudSettings, setCloudSettings] = useState<CloudSettings | null>(null);
  const cloudAvailable = cloudSettings !== null;
  const [prepay, setPrepay] = useState(true);
  const [counter, setCounter] = useState(false);
  const [timingBusy, setTimingBusy] = useState(false);
  const [cloudError, setCloudError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [state, setState] = useState<State | null>(null);
  const [provider, setProvider] = useState(NONE);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [confirmWipe, setConfirmWipe] = useState(false);

  useEffect(() => {
    let disposed = false;
    const cloud = loadCloudSettings().catch(() => {
      if (!disposed) setCloudError("Подключение Эвотора временно недоступно. Общие настройки и другие кассы доступны.");
      return null;
    });
    Promise.all([get<State>("/kassa/"), get<{ prepay_required: boolean; service_mode: string }>("/site/"), cloud])
      .then(([next, site, link]) => {
        if (disposed) return;
        setPrepay(site.prepay_required); setCounter(site.service_mode === "counter");
        setCloudSettings(link);
        apply(next.provider === NONE && link?.device_id ? { ...next, provider: EVOTOR } : next);
      })
      .catch(e => { if (!disposed) setLoadError(e instanceof Error ? e.message : "Не удалось загрузить настройки кассы"); });
    return () => { disposed = true; };
  }, []);

  function apply(next: State) {
    setState(next);
    setProvider(next.provider);
    // Несекретные поля показываем такими, как сохранены; секретные — пустыми.
    setValues(next.values);
    setConfirmWipe(false);
  }

  async function saveTiming(next: boolean) {
    setTimingBusy(true);
    try {
      await patch("/site/", { prepay_required: next });
      setPrepay(next);
      notify("Порядок оплаты сохранён для новых заказов", "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Не удалось сохранить", "bad"); }
    finally { setTimingBusy(false); }
  }

  if (loadError) return <p className="error" role="alert">{loadError}. Обновите страницу, чтобы повторить загрузку настроек.</p>;
  if (!state) return <p className="muted" role="status">Загружаю настройки кассы…</p>;

  const kind = state.kassas.find((k) => k.name === provider);
  const same = provider === state.provider;
  const filled = same ? state.filled : {};

  async function save() {
    setSaving(true);
    try {
      if (provider === NONE) {
        const link = await loadCloudSettings();
        if (link?.device_id) {
          const disabled = await post<CloudSettings>("/evotor-cloud/admin/settings-save/", {
            device_id: "", tax: "", revision: link.revision,
          });
          setCloudSettings({ ...link, ...disabled });
        }
      }
      const next = await put<State>("/kassa/", { provider, values });
      apply(next);
      notify(provider === NONE ? "Касса отключена" : "Касса подключена", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  async function resync() {
    setSaving(true);
    try {
      const res = await post<{ detail: string }>("/kassa/resync/", {});
      notify(res.detail, "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось синхронизировать", "bad");
    } finally {
      apply(await get<State>("/kassa/").catch(() => state!));
      setSaving(false);
    }
  }

  async function wipe() {
    setSaving(true);
    try {
      const next = await del<State>("/kassa/");
      apply(next);
      notify("Касса отключена, ключ удалён", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось удалить", "bad");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {counter && <div className="card mb-3">
        <strong className="title">Когда принимать оплату</strong>
        <div className="wrap mt-2">
          <button type="button" className={`btn sm ${prepay ? '' : 'ghost'}`} disabled={timingBusy} aria-pressed={prepay} onClick={() => saveTiming(true)}>До приготовления</button>
          <button type="button" className={`btn sm ${!prepay ? '' : 'ghost'}`} disabled={timingBusy} aria-pressed={!prepay} onClick={() => saveTiming(false)}>После приготовления</button>
        </div>
        <p className="muted sm">Общая настройка заведения для Эвотора, aQsi и других касс. Применяется к новым заказам. Уже созданные заказы сохраняют прежний порядок. Выдача — после оплаты.</p>
      </div>}
      <h2 className="section-title">Касса</h2>
      {cloudError && <p className="muted" role="status">{cloudError}</p>}
      <div className="card">
        <strong className="title">Оплата на кассе</strong>
        <p className="muted subtitle m-0">
          {provider === EVOTOR
            ? "Передавайте заказ кнопкой на стойке. После оплаты на Эвоторе подтверждение вернётся в заказ."
            : state.ready
            ? "Гость видит кнопку «Оплатить на кассе»: заказ уходит на кассу, после оплаты сам идёт в работу"
            : "Подключите кассу — и заказ с сайта будет приходить на неё с позициями, вбивать не придётся"}
        </p>

        <div className="rule-top mt-3">
          <label className="field mt-3">
            <span className="label">Касса</span>
            <select
              className="input"
              value={provider}
              onChange={(e) => {
                setProvider(e.target.value);
                setValues(e.target.value === state.provider ? state.values : {});
              }}
            >
              <option value={NONE}>Нет — оплату отмечает бариста</option>
              {(cloudAvailable || provider === EVOTOR) && <option value={EVOTOR}>Эвотор</option>}
              {state.kassas.map((k) => (
                <option key={k.name} value={k.name}>{k.title}</option>
              ))}
            </select>
          </label>

          {provider === EVOTOR ? (cloudSettings ? <EvotorConnection settings={cloudSettings} onSaved={next => { setCloudSettings(next); setState({ ...state, provider: EVOTOR }); }} /> : <p role="status">Не удалось загрузить подключение Эвотора. Обновите страницу.</p>) : <>
          {kind?.fields.map((f) => (
            <label className="field" key={f.key}>
              <span className="label">
                {f.label}
                {!f.required && <span className="muted"> — необязательно</span>}
              </span>
              {f.choices.length ? (
                <select
                  className="input"
                  value={values[f.key] || f.default}
                  onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                >
                  {f.choices.map((c) => (
                    <option key={c.value} value={c.value}>{c.label}</option>
                  ))}
                </select>
              ) : (
                <input
                  className="input"
                  type={f.secret ? "password" : "text"}
                  autoComplete="off"
                  autoCapitalize="none"
                  value={values[f.key] ?? ""}
                  onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                  placeholder={filled[f.key] && f.secret ? "Задан — оставьте пустым, чтобы не менять" : f.hint}
                />
              )}
              {f.hint && !f.secret && !f.choices.length && (
                <span className="muted sm">{f.hint}</span>
              )}
            </label>
          ))}

          <div className="wrap mt-3">
            <button className="btn sm" disabled={saving} onClick={save}>
              <Icon name="check" size={15} /> {saving ? "Проверяем…" : "Сохранить"}
            </button>
            {state.provider !== NONE &&
              (confirmWipe ? (
                <>
                  <span className="muted sm" style={{ alignSelf: "center" }}>
                    Отключить кассу и стереть ключ?
                  </span>
                  <button className="btn sm danger" disabled={saving} onClick={wipe}>
                    Да, отключить
                  </button>
                  <button className="btn sm ghost" onClick={() => setConfirmWipe(false)}>
                    Нет
                  </button>
                </>
              ) : (
                <button className="btn sm ghost" onClick={() => setConfirmWipe(true)}>
                  Отключить кассу
                </button>
              ))}
          </div>

          {/* Гость на «Оплатить на кассе» видит лишь «подойдите к баристе» —
              причину отказа кассы знает только владелец, отсюда. Пропадает
              сама, когда заказ снова уйдёт на кассу или касса примет ключ. */}
          {same && state.last_error && (
            <div className="rule-top mt-3 pt-3">
              <strong className="title">Заказы не уходят на кассу</strong>
              <p className="muted sm m-0">
                {when(state.last_error.at)}
                {state.last_error.order ? `, заказ №${state.last_error.order}` : ""} — касса ответила:
              </p>
              <p className="error sm m-0">{state.last_error.error}</p>
              <p className="muted sm mt-2 m-0">
                Гость видит «Подойдите к баристе», заказ ждёт в «Ждут оплаты». Проверьте ключ
                ниже или кабинет кассы; сообщение исчезнет, когда заказ снова уйдёт на кассу.
              </p>
            </div>
          )}

          {same && state.sync && (
            <div className="rule-top mt-3">
              <strong className="title mt-3">Синхронизация с терминалом</strong>
              <p className="muted sm m-0">
                Заказ есть в кабинете aQsi, а на кассу не пришёл — бариста жмёт «Синхронизировать»
                на доске стойки — как кнопку в кабинете.{" "}
                {state.sync.synced_at
                  ? `Последняя: ${when(state.sync.synced_at)}.`
                  : "Ещё не синхронизировали."}
              </p>
              {state.sync.error && <p className="error sm m-0">{state.sync.error}</p>}
              <button className="btn sm ghost mt-2" disabled={saving} onClick={resync}>
                <Icon name="spark" size={15} /> Синхронизировать сейчас
              </button>
            </div>
          )}

          <p className="muted sm mt-3 m-0">
            {state.ready
              ? "Ключ задан и проверен у кассы. Мы его не показываем — даже вам."
              : provider !== NONE
                ? "При сохранении ключ проверяется у кассы — неверный не сохранится."
                : "Выберите кассу, которая стоит у вас на точке."}
          </p>
          </>}
        </div>
      </div>
    </>
  );
}
