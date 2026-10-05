import { useEffect, useState } from "react";
import { del, get, patch, put, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite } from "../../site";

/** Раздел «Оплата картой» в панели владельца.

    Здесь владелец сам выбирает банк и вводит его доступы — раньше ключи
    прописывал разработчик в .env на сервере, и подключение эквайринга
    упиралось в него. В общей установке так и нельзя: окружение одно на
    все заведения, а магазин у каждого свой.

    Данные берём из /api/acquiring/, а не из /api/site/: тот публичный, и
    ни банку, ни состоянию его доступов там не место.

    Значения полей сюда не приходят никогда, даже владельцу — только
    «задано». Поэтому пустое поле при сохранении означает «не менял», а
    стереть доступы можно лишь целиком, отдельной кнопкой.
*/
type BankField = {
  key: string;
  label: string;
  hint: string;
  secret: boolean;
  required: boolean;
  /** Выбор из списка вместо ввода (ставка НДС). */
  choices: { value: string; label: string }[];
  default: string;
};

type Bank = { name: string; title: string; fields: BankField[] };

type State = {
  provider: string;
  ready: boolean;
  online_payment_on: boolean;
  filled: Record<string, boolean>;
  /** Значения несекретных полей — чтобы список показал, что выбрано. */
  values: Record<string, string>;
  /** Подключена ли к банку онлайн-касса (чек по 54-ФЗ); null — неизвестно. */
  receipts: boolean | null;
  /** Адрес, который владелец вписывает в кабинете банка (пусто — банка нет). */
  callback_url: string;
  /** Банк получает адрес с каждым платежом — в кабинет вписывать не нужно. */
  callback_auto: boolean;
  /** Последний отказ банка, после которого оплата ещё не проходила. */
  last_error: { error: string; at: string; order: number | null } | null;
  banks: Bank[];
};

const NONE = "none";

const when = (iso: string) =>
  new Date(iso).toLocaleString("ru", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });

export default function OnlinePayment() {
  const notify = useToast();
  const site = useSite();
  // Правку держим локально: контекст сайта перечитывается при загрузке
  // страницы, а ждать этого ради одного выключателя незачем.
  const [prepay, setPrepay] = useState<boolean | null>(null);
  const [state, setState] = useState<State | null>(null);
  const [provider, setProvider] = useState(NONE);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [confirmWipe, setConfirmWipe] = useState(false);

  useEffect(() => {
    get<State>("/acquiring/")
      .then((s) => {
        setState(s);
        setProvider(s.provider);
      })
      .catch(() => {});
  }, []);

  if (!state) return null;

  const bank = state.banks.find((b) => b.name === provider);
  const chosen = provider !== NONE;
  // Банк ещё не сохранён — «задано» относится к прежнему, показывать его
  // на полях нового было бы враньём.
  const filled = provider === state.provider ? state.filled : {};
  const live = state.ready && state.online_payment_on;

  function apply(next: State) {
    setState(next);
    setProvider(next.provider);
    setValues({});
    setConfirmWipe(false);
  }

  async function save() {
    setSaving(true);
    try {
      apply(await put<State>("/acquiring/", { provider, values }));
      notify(chosen ? "Доступы сохранены" : "Онлайн-оплата отключена", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  async function wipe() {
    setSaving(true);
    try {
      apply(await del<State>("/acquiring/"));
      notify("Доступы удалены", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось удалить", "bad");
    } finally {
      setSaving(false);
    }
  }

  async function toggle() {
    if (!state) return;
    const next = !state.online_payment_on;
    setSaving(true);
    try {
      await patch("/site/", { online_payment_on: next });
      setState({ ...state, online_payment_on: next });
      notify(next ? "Оплата картой включена" : "Оплата картой выключена", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  const counter = site?.service_mode === "counter";
  // Предоплате нужно, чтобы гостю было чем платить: онлайн или на кассе.
  const payable = live || site?.kassa_payment === true;
  const prepayOn = prepay ?? site?.prepay_required ?? true;

  /** Готовить только после оплаты — смысл есть лишь на стойке (см. ниже). */
  async function togglePrepay() {
    const next = !prepayOn;
    setSaving(true);
    try {
      await patch("/site/", { prepay_required: next });
      setPrepay(next);
      notify(
        next ? "Заказ пойдёт на кухню после оплаты" : "Заказ идёт на кухню сразу",
        "ok",
      );
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
                ? "Гость видит кнопку «Оплатить онлайн» в своём заказе"
                : "Гость платит наличными или картой у официанта"}
            </p>
          </div>
          {/* Выключатель отдельный от банка: банк с ключами подключают один
              раз, а закрыть оплату может понадобиться в любой момент —
              банк лёг, день только за наличные. Без доступов не даём
              включить: гость упёрся бы в ошибку, а виноватым выглядело
              бы кафе. */}
          <button
            className={"btn sm" + (state.online_payment_on ? "" : " ghost")}
            disabled={saving || !state.ready}
            onClick={toggle}
          >
            <Icon name={state.online_payment_on ? "check" : "close"} size={15} />
            {state.online_payment_on ? "Включена" : "Выключена"}
          </button>
        </div>

        {/* Предоплата — только для стойки. В зале за стол отвечает
            официант: там заявка нужна ему как раз до оплаты, иначе он
            не примет гостя вовсе. */}
        {counter && (
          <div className="between rule-top mt-3 pt-3">
            <div>
              <strong className="title">Готовить только после оплаты</strong>
              <p className="muted subtitle m-0">
                {prepayOn
                  ? "Заказ по QR ждёт оплаты — картой или на кассе: бар начнёт, когда придут деньги"
                  : "Заказ уходит на кухню сразу, гость платит при выдаче"}
              </p>
            </div>
            <button
              className={"btn sm" + (prepayOn ? "" : " ghost")}
              disabled={saving || !payable}
              onClick={togglePrepay}
            >
              <Icon name={prepayOn ? "check" : "close"} size={15} />
              {prepayOn ? "Включено" : "Выключено"}
            </button>
          </div>
        )}
        {counter && prepayOn && !payable && (
          <p className="muted sm mt-2 m-0">
            Пока не работают ни оплата картой, ни касса, заказы принимаются как раньше —
            платить гостю было бы нечем.
          </p>
        )}

        {/* Отказ банка гость видит общей фразой «не получилось перейти к
            оплате» — причину знает только владелец, отсюда. Иначе протухший
            ключ находился бы по жалобам гостей. Пропадает сама, как только
            следующая оплата пройдёт или банк примет новые ключи. */}
        {state.last_error && state.provider === provider && (
          <div className="rule-top mt-3 pt-3">
            <strong className="title">Гости не могут перейти к оплате</strong>
            <p className="muted sm m-0">
              {when(state.last_error.at)}
              {state.last_error.order ? `, заказ №${state.last_error.order}` : ""} — банк ответил:
            </p>
            <p className="error sm m-0">{state.last_error.error}</p>
            <p className="muted sm mt-2 m-0">
              Гость видит «Не получилось перейти к оплате» и платит на месте. Проверьте
              ключи ниже или кабинет банка; сообщение исчезнет, когда оплата снова пройдёт.
            </p>
          </div>
        )}

        <div className="rule-top mt-3">
          <label className="field mt-3">
            <span className="label">Банк</span>
            <select
              className="input"
              value={provider}
              onChange={(e) => {
                setProvider(e.target.value);
                setValues({});
              }}
            >
              <option value={NONE}>Нет онлайн-оплаты</option>
              {state.banks.map((b) => (
                <option key={b.name} value={b.name}>{b.title}</option>
              ))}
            </select>
          </label>

          {bank?.fields.map((f) => (
            <label className="field" key={f.key}>
              <span className="label">
                {f.label}
                {!f.required && <span className="muted"> — необязательно</span>}
              </span>
{f.choices.length ? (
                <select
                  className="input"
                  value={
                    values[f.key] ??
                    ((provider === state.provider && state.values[f.key]) || f.default)
                  }
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
                placeholder={filled[f.key] ? "Задан — оставьте пустым, чтобы не менять" : f.hint}
              />
              )}
            </label>
          ))}

          <div className="wrap mt-3">
            <button className="btn sm" disabled={saving} onClick={save}>
              <Icon name="check" size={15} /> Сохранить
            </button>
            {state.provider !== NONE &&
              (confirmWipe ? (
                <>
                  <span className="muted sm" style={{ alignSelf: "center" }}>
                    Стереть доступы банка?
                  </span>
                  <button className="btn sm danger" disabled={saving} onClick={wipe}>
                    Да, стереть
                  </button>
                  <button className="btn sm ghost" onClick={() => setConfirmWipe(false)}>
                    Нет
                  </button>
                </>
              ) : (
                <button className="btn sm ghost" onClick={() => setConfirmWipe(true)}>
                  Стереть доступы
                </button>
              ))}
          </div>

          {/* Уведомление банка приходит на этот адрес, и прописывают его
              в кабинете банка руками. Пока он не прописан, оплаченный
              заказ закрывается с задержкой — опросом, — а гость успевает
              увидеть кнопку «оплатить» на уже оплаченном заказе. */}
          {state.ready && state.callback_url && (
            <div className="rule-top mt-3 pt-3">
              <span className="label">Адрес для уведомлений банка</span>
              <p className="muted sm m-0">
                {state.callback_auto
                  ? "Мы сообщаем его банку с каждым платежом — в кабинете ничего вписывать не нужно."
                  : "Впишите его в кабинете банка — тогда заказ закроется сразу после оплаты."}
              </p>
              <div className="wrap mt-2">
                <code className="sm" style={{ wordBreak: "break-all", alignSelf: "center" }}>
                  {state.callback_url}
                </code>
                <button
                  className="btn sm ghost"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(state.callback_url);
                      notify("Адрес скопирован", "ok");
                    } catch {
                      notify("Скопируйте адрес вручную", "bad");
                    }
                  }}
                >
                  <Icon name="copy" size={15} /> Скопировать
                </button>
              </div>
            </div>
          )}

          {/* Касса у банка — значит, банк не примет платёж без чека: гостя
              спросят телефон или почту, а позиции и НДС уйдут в чек сами. */}
          {state.ready && state.receipts !== null && provider === state.provider && (
            <p className="muted sm mt-3 m-0">
              {state.receipts
                ? "К банку подключена онлайн-касса: чек пробивается сам. Перед оплатой гостя спросим телефон или почту — туда придёт чек."
                : "Онлайн-касса к банку не подключена — чек по онлайн-оплате нужно пробивать самим."}
            </p>
          )}

          <p className="muted sm mt-3 m-0">
            {state.ready
              ? "Ключи заданы. Мы их не показываем — даже вам: увидеть их сможет любой, кто сядет за открытую панель."
              : chosen
                ? "Ключи выдаёт личный кабинет банка. Пока их нет, оплату картой включить нельзя."
                : "Выберите банк, с которым у заведения договор, и введите его ключи из личного кабинета."}
          </p>
        </div>
      </div>
    </>
  );
}
