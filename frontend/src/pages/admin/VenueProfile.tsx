import { useState } from "react";
import { patch, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite, useUpdateSite } from "../../site";
import type { Site } from "../../types";

/** Раздел «Заведение и реквизиты» — то, что гость видит в подвале сайта и
    на юр. страницах: название, контакты, ИНН, счёт.

    Раньше всё это правилось только в Django-админке, то есть руками
    «Падачи». Это данные заведения, менять их должен администратор
    сам — сменился телефон, переоформили ИП. Проверку цифр реквизитов
    держит бэк (core/serializers.py), ошибки показываем под полями.
*/

type Key = keyof Site & string;
type Field = {
  key: Key;
  label: string;
  placeholder?: string;
  hint?: string;
  long?: boolean; // многострочное поле
  max?: number;
  numeric?: boolean;
};

const VENUE: Field[] = [
  { key: "name", label: "Название", placeholder: "Как заведение называют гости" },
  { key: "tagline", label: "Слоган", placeholder: "Под названием в подвале" },
  {
    key: "app_short_name",
    label: "Подпись под иконкой",
    max: 12,
    hint: "До 12 символов — на телефоне длиннее обрежется. Пусто — возьмём из названия.",
  },
  { key: "phone", label: "Телефон", placeholder: "+7 900 000-00-00" },
  { key: "email", label: "Почта", placeholder: "cafe@mail.ru" },
  { key: "address", label: "Адрес", placeholder: "Город, улица, дом" },
  { key: "working_hours", label: "Часы работы", placeholder: "Ежедневно 9:00–22:00" },
  { key: "instagram", label: "Instagram", placeholder: "ник без @" },
  { key: "telegram", label: "Telegram", placeholder: "ник или канал без @" },
  { key: "about", label: "О заведении", long: true, placeholder: "Пара строк для подвала сайта" },
];

const REQUISITES: Field[] = [
  { key: "merchant_type", label: "Форма", placeholder: "Индивидуальный предприниматель" },
  { key: "merchant_name", label: "Полное наименование", placeholder: "Иванова Мария Леонидовна" },
  { key: "merchant_short", label: "Краткое наименование", placeholder: "ИП Иванова М. Л." },
  { key: "merchant_address", label: "Юридический адрес" },
  { key: "merchant_inn", label: "ИНН", numeric: true },
  { key: "merchant_ogrn", label: "ОГРН / ОГРНИП", numeric: true },
  { key: "merchant_account", label: "Расчётный счёт", numeric: true },
  { key: "merchant_bank", label: "Банк", placeholder: "АО «ТБанк»" },
  { key: "merchant_bik", label: "БИК", numeric: true },
  { key: "merchant_corr_account", label: "Корр. счёт", numeric: true },
  { key: "merchant_bank_inn", label: "ИНН банка", numeric: true },
  { key: "merchant_bank_address", label: "Адрес банка" },
  { key: "acquirer", label: "Кто принимает оплату картой", placeholder: "АО «ТБанк» (Т-Касса)" },
  {
    key: "legal_updated",
    label: "Дата редакции документов",
    placeholder: "3 августа 2026 г.",
    hint: "Показывается в оферте и политике. Обновите, если поменялись реквизиты.",
  },
];

const ALL = [...VENUE, ...REQUISITES];

type Draft = Record<string, string>;

function draftFrom(site: Site): Draft {
  return Object.fromEntries(ALL.map((f) => [f.key, String(site[f.key] ?? "")]));
}

export default function VenueProfile() {
  const site = useSite();
  const updateSite = useUpdateSite();
  const notify = useToast();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  if (!site) return null;

  const open = () => {
    setErrors({});
    setDraft(draftFrom(site));
  };

  const save = async () => {
    if (!draft) return;
    const initial = draftFrom(site);
    const changed: Draft = {};
    for (const f of ALL) {
      let value = draft[f.key].trim();
      // Ник соцсети подставляется в ссылку: «@monti» дал бы instagram.com/@monti.
      if (f.key === "instagram" || f.key === "telegram") value = value.replace(/^@/, "");
      if (value !== initial[f.key]) changed[f.key] = value;
    }
    if (!Object.keys(changed).length) {
      setDraft(null);
      return;
    }
    setSaving(true);
    setErrors({});
    try {
      const fresh = await patch<Site>("/site/", changed);
      updateSite(fresh);
      setDraft(null);
      notify("Сохранено — гости уже видят новые данные", "ok");
    } catch (e) {
      // DRF отвечает ошибками по полям: {"merchant_inn": ["ИНН — 10 цифр…"]}.
      let fieldErrors: Record<string, string> = {};
      if (e instanceof ApiError) {
        try {
          const body = JSON.parse(e.message) as Record<string, string[] | string>;
          fieldErrors = Object.fromEntries(
            Object.entries(body).map(([k, v]) => [k, Array.isArray(v) ? v.join(" ") : v]),
          );
        } catch {
          /* не JSON — общий текст ниже */
        }
      }
      setErrors(fieldErrors);
      notify(
        Object.keys(fieldErrors).length
          ? "Проверьте отмеченные поля"
          : e instanceof ApiError
            ? e.message
            : "Не удалось сохранить",
        "bad",
      );
    } finally {
      setSaving(false);
    }
  };

  const renderField = (f: Field) => {
    if (!draft) return null;
    const props = {
      className: "input",
      value: draft[f.key],
      placeholder: f.placeholder,
      maxLength: f.max,
      disabled: saving,
      onChange: (e: { target: { value: string } }) =>
        setDraft({ ...draft, [f.key]: e.target.value }),
    };
    return (
      <label className="field" key={f.key}>
        <span className="label">{f.label}</span>
        {f.long ? (
          <textarea rows={3} {...props} />
        ) : (
          <input {...props} inputMode={f.numeric ? "numeric" : undefined} />
        )}
        {errors[f.key] ? (
          <span className="sm label-bad">{errors[f.key]}</span>
        ) : (
          f.hint && <span className="muted sm">{f.hint}</span>
        )}
      </label>
    );
  };

  const filled = site.merchant_inn && site.merchant_name;

  return (
    <>
      <h2 className="section-title">Заведение и реквизиты</h2>
      <div className="card">
        {!draft ? (
          <div className="between">
            <div className="inline">
              <span className="tx-icon">
                <Icon name="store" size={18} />
              </span>
              <div>
                <strong className="title">{site.name || "Без названия"}</strong>
                <p className="muted subtitle m-0">
                  {[site.phone, site.email].filter(Boolean).join(" · ") || "Контакты не указаны"}
                  {" · "}
                  {filled ? `ИНН ${site.merchant_inn}` : "реквизиты не заполнены"}
                </p>
              </div>
            </div>
            <button className="btn sm ghost" onClick={open}>
              Изменить
            </button>
          </div>
        ) : (
          <>
            <p className="muted m-0">
              Это видят гости: в подвале сайта, в оферте и на странице
              «Реквизиты и контакты».
            </p>
            <h3 className="mt-3">Заведение</h3>
            <div className="form-grid">{VENUE.map(renderField)}</div>
            <h3 className="mt-3">Реквизиты продавца</h3>
            <p className="muted sm m-0">
              Как в выписке из ЕГРИП/ЕГРЮЛ и договоре с банком — банк сверяет их
              с сайтом. Пробелы в номерах можно не убирать.
            </p>
            <div className="form-grid">{REQUISITES.map(renderField)}</div>
            <div className="wrap mt-3">
              <button className="btn" disabled={saving} onClick={save}>
                {saving ? "Сохраняем…" : "Сохранить"}
              </button>
              <button className="btn ghost" disabled={saving} onClick={() => setDraft(null)}>
                Отмена
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
