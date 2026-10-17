import { useEffect, useState, type FormEvent } from "react";
import { get, post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import type { LoyaltyMember } from "../../types";
import BonusImport from "./BonusImport";
import MemberCard from "./MemberCard";

const SOURCE: Record<string, string> = {
  guest: "сам",
  staff: "персонал",
  import: "перенос",
};

/** «2 ноября» из «1988-11-02». Не через new Date(строка): она читается
    как полночь по UTC, и западнее Гринвича день рождения съехал бы на день. */
function birthday(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("ru", { day: "numeric", month: "long" });
}

type Sort = "new" | "balance" | "name";
const SORTS: { key: Sort; label: string }[] = [
  { key: "new", label: "Новые" },
  { key: "balance", label: "Больше бонусов" },
  { key: "name", label: "По имени" },
];

/** Гости бонусной программы на странице «Бонусы».

    Список виден сразу и догружается по 50; поиск по имени или телефону,
    касание открывает карточку гостя. Гостя из прежней системы заводят с
    его остатком: тогда приветственных нет — он их уже получал там.
*/
export default function BonusGuests({ welcome }: { welcome: number }) {
  const notify = useToast();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("new");
  const [rows, setRows] = useState<LoyaltyMember[]>([]);
  const [count, setCount] = useState<number | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const EMPTY = { name: "", phone: "", birth: "", transfer: false, balance: "", consent: false };
  const [form, setForm] = useState(EMPTY);
  // Отмена очищает форму: иначе следующий гость открылся бы с данными
  // и галочками прошлого, и согласие ушло бы за человека, которого не спросили.
  const closeForm = () => {
    setForm(EMPTY);
    setAdding(false);
  };

  function url(offset: number) {
    const query = q.trim();
    return (
      `/loyalty/members/?sort=${sort}&offset=${offset}` +
      (query.length >= 2 ? `&q=${encodeURIComponent(query)}` : "")
    );
  }

  async function load(more = false) {
    setLoading(true);
    try {
      const d = await get<{ count: number; results: LoyaltyMember[] }>(
        url(more ? rows.length : 0)
      );
      setRows((r) => (more ? [...r, ...d.results] : d.results));
      setCount(d.count);
      // всего в программе — число без поиска
      if (q.trim().length < 2) setTotal(d.count);
    } catch {
      if (!more) setRows([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // поиск по мере ввода, но без запроса на каждую букву
    const t = setTimeout(() => load(false), 250);
    return () => clearTimeout(t);
  }, [q, sort]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Гость изменился в карточке — обновить строку, не перечитывая список. */
  function patchRow(m: LoyaltyMember) {
    setRows((r) => r.map((x) => (x.id === m.id ? { ...x, ...m } : x)));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!form.consent) {
      notify("Отметьте согласие гостя на обработку данных", "bad");
      return;
    }
    setBusy(true);
    try {
      const m = await post<LoyaltyMember>("/loyalty/members/", {
        name: form.name.trim(),
        phone: form.phone,
        consent: true,
        ...(form.birth ? { birth_date: form.birth } : {}),
        ...(form.transfer ? { transfer_balance: form.balance || "0" } : {}),
      });
      notify(`${m.name} в программе · ${Number(m.balance).toLocaleString("ru")} бонусов`, "ok");
      closeForm();
      setTotal((t) => (t ?? 0) + 1);
      setQ(m.phone); // показываем только что добавленного
      setOpenId(m.id);

    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Не удалось добавить гостя", "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card mt-3">
      <div className="between">
        <div>
          <strong className="title">Гости программы</strong>
          <p className="muted subtitle m-0">
            {total == null ? "Загружаем…" : `Всего ${total.toLocaleString("ru")}`}
          </p>
        </div>
        {!adding && !importing && (
          <div className="wrap">
            <button className="btn sm ghost" onClick={() => setImporting(true)}>
              <Icon name="download" size={15} /> Из файла
            </button>
            <button className="btn sm" onClick={() => setAdding(true)}>
              <Icon name="plus" size={15} /> Добавить гостя
            </button>
          </div>
        )}
      </div>

      {importing && (
        <BonusImport
          onClose={() => setImporting(false)}
          onDone={(imported) => {
            setImporting(false);
            setTotal((t) => (t ?? 0) + imported);
            load(false);
          }}
        />
      )}

      {adding && (
        <form className="rule-top mt-3" onSubmit={submit}>
          <div className="wrap">
            <input
              className="input grow"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Имя"
              maxLength={150}
              required
            />
            <input
              className="input grow"
              value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })}
              placeholder="+7 999 000-00-00"
              inputMode="tel"
              required
            />
            <input
              className="input"
              style={{ width: 160 }}
              type="date"
              value={form.birth}
              onChange={(e) => setForm({ ...form, birth: e.target.value })}
              aria-label="Дата рождения"
            />
          </div>

          <label className="inline tight mt-3">
            <input
              type="checkbox"
              checked={form.transfer}
              onChange={(e) => setForm({ ...form, transfer: e.target.checked })}
            />
            <span className="sm">Переносим из прежней системы</span>
          </label>
          {form.transfer ? (
            <div className="wrap mt-2" style={{ alignItems: "center" }}>
              <input
                className="input"
                style={{ width: 160 }}
                value={form.balance}
                onChange={(e) => setForm({ ...form, balance: e.target.value.replace(/[^\d]/g, "") })}
                placeholder="Остаток бонусов"
                inputMode="numeric"
              />
              <span className="muted sm">
                Остаток зачислится как есть, приветственных не будет
              </span>
            </div>
          ) : (
            <p className="muted sm m-0 mt-1">
              Новому гостю сразу начислим {welcome.toLocaleString("ru")} приветственных бонусов
            </p>
          )}

          <label className="inline tight mt-3">
            <input
              type="checkbox"
              checked={form.consent}
              onChange={(e) => setForm({ ...form, consent: e.target.checked })}
            />
            <span className="sm">Гость согласен на обработку персональных данных</span>
          </label>

          <div className="wrap mt-3">
            <button className="btn sm" disabled={busy || !form.consent}>
              <Icon name="check" size={15} /> Добавить
            </button>
            <button type="button" className="btn sm ghost" onClick={closeForm}>
              Отмена
            </button>
          </div>
        </form>
      )}

      <input
        className="input mt-3"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Поиск по имени или телефону"
      />
      <div className="size-row">
        {SORTS.map((o) => (
          <button
            key={o.key}
            type="button"
            className={"size-chip" + (sort === o.key ? " active" : "")}
            onClick={() => setSort(o.key)}
          >
            {o.label}
          </button>
        ))}
      </div>

      {!loading && rows.length === 0 ? (
        <p className="muted sm center mt-3">
          {q.trim().length >= 2 ? "Никого не нашли" : "Гостей пока нет"}
        </p>
      ) : (
        <ul className="stack tight list mt-3">
          {rows.map((m) => (
            <li key={m.id}>
              <button className="member-row" onClick={() => setOpenId(m.id)}>
                <div className="between">
                  <strong>{m.name}</strong>
                  <span className="num" style={{ whiteSpace: "nowrap" }}>
                    {Number(m.balance).toLocaleString("ru")} б.
                  </span>
                </div>
                <div className="muted sm">
                  {m.phone}
                  {m.birth_date ? ` · ДР ${birthday(m.birth_date)}` : ""}
                  {m.source ? ` · ${SOURCE[m.source]}` : ""}
                  {m.earn_percent != null
                    ? ` · личный ${Number(m.earn_percent).toLocaleString("ru")} %`
                    : ""}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
      {count != null && rows.length < count && (
        <button className="btn sm ghost block mt-2" onClick={() => load(true)} disabled={loading}>
          Показать ещё · {rows.length} из {count.toLocaleString("ru")}
        </button>
      )}

      {openId != null && (
        <MemberCard id={openId} onClose={() => setOpenId(null)} onChanged={patchRow} />
      )}
    </div>
  );
}
