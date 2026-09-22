import { useEffect, useState } from "react";
import { del, get, patch, put, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";

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
};

type Bank = { name: string; title: string; fields: BankField[] };

type State = {
  provider: string;
  ready: boolean;
  online_payment_on: boolean;
  filled: Record<string, boolean>;
  banks: Bank[];
};

const NONE = "none";

export default function OnlinePayment() {
  const notify = useToast();
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
              <input
                className="input"
                type={f.secret ? "password" : "text"}
                autoComplete="off"
                autoCapitalize="none"
                value={values[f.key] ?? ""}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                placeholder={filled[f.key] ? "Задан — оставьте пустым, чтобы не менять" : f.hint}
              />
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
