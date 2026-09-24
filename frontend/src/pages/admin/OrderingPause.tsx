import { useState } from "react";
import { patch, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite } from "../../site";

/** Раздел «Заказы с сайта» — технический перерыв.

    Владелец закрывает приём заказов по QR, не трогая ничего больше:
    кофемашина встала, кухня захлебнулась, закрылись раньше срока. Меню
    гость по-прежнему видит — он должен понимать, куда попал, и прочитать
    причину, а не упереться в кнопку, которая молча не работает.

    Персонала перерыв не касается: официант принимает заказы как обычно, а
    уже принятый заказ гость дооплачивает и отслеживает. Настоящий запрет
    держит бэк (orders/services.create_request), здесь только выключатель.
*/
export default function OrderingPause() {
  const site = useSite();
  const notify = useToast();
  // Правку держим локально: контекст сайта читается один раз при загрузке
  // страницы и после сохранения показывал бы прежнее значение.
  const [paused, setPaused] = useState<boolean | null>(null);
  // Сохранённая причина и то, что владелец печатает сейчас, — разные вещи:
  // по уходу из поля их надо сравнить и не дёргать API, если не менялось.
  const [saved, setSaved] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const on = paused ?? site?.ordering_paused ?? false;
  const note = saved ?? site?.ordering_pause_note ?? "";
  const text = draft ?? note;

  async function toggle() {
    const next = !on;
    setSaving(true);
    try {
      await patch("/site/", { ordering_paused: next });
      setPaused(next);
      notify(next ? "Заказы с сайта остановлены" : "Заказы с сайта снова принимаются", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  /** Причину сохраняем по уходу из поля: отдельная кнопка «ОК» тут лишняя. */
  async function saveNote(value: string) {
    if (value === note) {
      setDraft(value);  // подчистить лишние пробелы в поле
      return;
    }
    setSaving(true);
    try {
      await patch("/site/", { ordering_pause_note: value });
      setSaved(value);
      setDraft(value);
      notify(value ? "Причина сохранена" : "Причину убрали", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h2 className="section-title">Заказы с сайта</h2>
      <div className="card">
        <div className="between">
          <div className="inline">
            <span className="tx-icon">
              <Icon name={on ? "lock" : "check"} size={18} />
            </span>
            <div>
              <strong className="title">
                {on ? "Технический перерыв" : "Заказы принимаются"}
              </strong>
              <p className="muted subtitle m-0">
                {on
                  ? "Гость видит меню и причину перерыва, но отправить заказ не может"
                  : "Гость заказывает по QR как обычно"}
              </p>
            </div>
          </div>
          <button
            className={"btn sm" + (on ? "" : " ghost")}
            disabled={saving}
            onClick={toggle}
          >
            <Icon name={on ? "close" : "check"} size={15} />
            {on ? "Включён" : "Выключен"}
          </button>
        </div>

        <label className="field mt-3">
          <span className="label">Что показать гостю</span>
          <input
            className="input"
            value={text}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={(e) => saveNote(e.target.value.trim())}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
            placeholder="напр. Кофемашина на профилактике, ждём к 14:00"
            maxLength={200}
            disabled={saving}
          />
          <span className="muted sm">
            Пусто — гость увидит просто «Технический перерыв».
          </span>
        </label>

        {/* Заказы официанта остаются: перерыв закрывает гостевую дверь, а
            не заведение. Владельцу это стоит сказать прямо — иначе он
            побоится трогать выключатель в час пик. */}
        <p className="muted sm mt-3 m-0">
          Персонала не касается: официант и стойка принимают заказы как обычно,
          а уже принятые гости оплачивают и отслеживают.
        </p>
      </div>
    </>
  );
}
