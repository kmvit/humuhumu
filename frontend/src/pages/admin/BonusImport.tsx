import { useRef, useState } from "react";
import { postForm, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import type { MemberImport } from "../../types";

const FIELD: Record<string, string> = {
  phone: "телефон",
  name: "имя",
  birth_date: "день рождения",
  balance: "остаток бонусов",
};

const n = (v: string | number) => Number(v).toLocaleString("ru");

/** Сколько строк списка показывать, пока не попросят все. */
const PREVIEW = 8;

/** Перенос гостей из файла прежней системы: разбор → проверка глазами → перенос.

    Файл отправляется дважды — на разбор и на перенос: держать чужую базу
    гостей на сервере между шагами незачем, а разбор быстрый. */
export default function BonusImport({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: (imported: number) => void;
}) {
  const notify = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [plan, setPlan] = useState<MemberImport | null>(null);
  const [busy, setBusy] = useState(false);
  const [consent, setConsent] = useState(false);
  const [showAll, setShowAll] = useState(false);

  async function send(f: File, commit: boolean): Promise<MemberImport | null> {
    const form = new FormData();
    form.append("file", f);
    if (commit) {
      form.append("commit", "1");
      form.append("consent", "1");
    }
    setBusy(true);
    try {
      return await postForm<MemberImport>("/loyalty/members/import/", form);
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось прочитать файл", "bad");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function pick(f: File | undefined) {
    if (!f) return;
    setFile(f);
    setPlan(null);
    setConsent(false);
    setShowAll(false);
    setPlan(await send(f, false));
  }

  async function commit() {
    if (!file || !plan) return;
    const done = await send(file, true);
    if (!done) {
      // разбор мог устареть (кого-то завели вручную) — покажем свежий
      setPlan(await send(file, false));
      return;
    }
    notify(
      `Перенесено гостей: ${n(done.imported)} · бонусов ${n(done.balance_total)}`,
      "ok"
    );
    onDone(done.imported);
  }

  const rows = plan ? (showAll ? plan.to_import : plan.to_import.slice(0, PREVIEW)) : [];
  const cut = plan ? plan.to_import.reduce(
    (s, g) => s + Number(g.balance_in_file) - Number(g.balance), 0
  ) : 0;

  return (
    <div className="rule-top mt-3">
      <div className="between">
        <strong>Перенос из прежней системы</strong>
        <button className="btn sm ghost" onClick={onClose}>
          Отмена
        </button>
      </div>
      <p className="muted sm m-0 mt-1">
        Выгрузка клиентов в Excel (.xlsx) или CSV. Нужна колонка «Телефон»; имя, день
        рождения и остаток бонусов возьмём, если есть. Приветственных бонусов не
        будет, остаток зачислится отдельной проводкой. Кто уже есть в программе —
        пропустим, поэтому повторная загрузка ничего не удвоит.
      </p>

      <input
        ref={fileRef}
        type="file"
        accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
        hidden
        onChange={(e) => {
          pick(e.target.files?.[0]);
          e.target.value = ""; // тот же файл можно выбрать снова
        }}
      />
      <button className="btn sm mt-3" onClick={() => fileRef.current?.click()} disabled={busy}>
        <Icon name={busy && !plan ? "spark" : "download"} size={15} />{" "}
        {file ? "Выбрать другой файл" : "Выбрать файл"}
      </button>
      {file && <span className="muted sm" style={{ marginLeft: 8 }}>{file.name}</span>}

      {plan && (
        <div className="stack mt-3">
          <p className="muted sm m-0">
            Взяли колонки:{" "}
            {Object.entries(plan.columns)
              .map(([k, v]) => `${FIELD[k]} — «${v}»`)
              .join(", ")}
            {!plan.columns.balance && ". Колонки с остатком нет — перенесём без бонусов"}
          </p>

          <div className="between">
            <span>
              Перенесём <strong>{n(plan.to_import.length)}</strong> из {n(plan.total)}
            </span>
            <strong className="num">{n(plan.balance_total)} б.</strong>
          </div>
          {cut > 0.004 && (
            <p className="muted sm m-0">
              Бонусы у нас целые: дробная часть отбрасывается, всего{" "}
              {cut.toLocaleString("ru", { maximumFractionDigits: 2 })} б. (у гостя не
              больше 0,99).
            </p>
          )}

          {plan.to_import.length > 0 && (
            <ul className="stack tight list">
              {rows.map((g) => (
                <li key={g.row} className="between">
                  <span style={{ minWidth: 0 }}>
                    {g.name || <span className="muted">без имени</span>}
                    <span className="muted sm"> · {g.phone}</span>
                  </span>
                  <span className="num muted" style={{ whiteSpace: "nowrap" }}>
                    {n(g.balance)} б.
                  </span>
                </li>
              ))}
              {!showAll && plan.to_import.length > PREVIEW && (
                <li>
                  <button className="btn sm ghost" onClick={() => setShowAll(true)}>
                    Показать всех ({n(plan.to_import.length)})
                  </button>
                </li>
              )}
            </ul>
          )}

          {plan.skipped.length > 0 && (
            <details>
              <summary className="sm">Пропустим: {plan.skipped.length}</summary>
              <ul className="stack tight list mt-2">
                {plan.skipped.map((s) => (
                  <li key={s.row} className="sm">
                    стр. {s.row} · {s.name || s.phone} — {s.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {plan.invalid.length > 0 && (
            <details open>
              <summary className="sm" style={{ color: "var(--danger)" }}>
                Не разобрали: {plan.invalid.length}
              </summary>
              <ul className="stack tight list mt-2">
                {plan.invalid.map((s) => (
                  <li key={s.row} className="sm">
                    стр. {s.row}{s.name ? ` · ${s.name}` : ""} — {s.reason}
                  </li>
                ))}
              </ul>
              <p className="muted sm m-0 mt-1">
                Их можно поправить в файле и загрузить его снова — перенесённых
                второй раз не тронем.
              </p>
            </details>
          )}
          {plan.notes.length > 0 && (
            <details>
              <summary className="sm">Замечания: {plan.notes.length}</summary>
              <ul className="stack tight list mt-2">
                {plan.notes.map((s) => (
                  <li key={s.row} className="sm">
                    стр. {s.row}{s.name ? ` · ${s.name}` : ""} — {s.note}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {plan.to_import.length > 0 ? (
            <>
              <label className="inline tight">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                />
                <span className="sm">
                  Гости давали согласие на обработку персональных данных в прежней
                  программе
                </span>
              </label>
              <button className="btn" onClick={commit} disabled={busy || !consent}>
                <Icon name={busy ? "spark" : "check"} size={16} /> Перенести гостей:{" "}
                {n(plan.to_import.length)}
              </button>
            </>
          ) : (
            <p className="muted sm m-0">Переносить некого.</p>
          )}
        </div>
      )}
    </div>
  );
}
