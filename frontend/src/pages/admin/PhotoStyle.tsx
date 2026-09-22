import { useEffect, useState } from "react";
import { ApiError, get, patch, patchForm } from "../../api";
import Icon from "../../components/Icon";
import Modal from "../../components/ui/Modal";
import { useToast } from "../../components/ui/Toast";
import type { MenuImageSettings, PhotoStyle as Style } from "../../types";

/** «Как выглядят наши фото» — один стиль на всё меню заведения.

    Отдельно от студии нарочно: студия — это разовая генерация («нарисуй
    вот этим блюдам»), а здесь то, что задаётся однажды и потом молча
    работает на каждой карточке. Смешать их значило бы заставлять владельца
    каждый раз проходить мимо настроек, которые он уже выбрал.
*/

const STYLES: { id: Style; label: string; hint: string }[] = [
  { id: "studio", label: "Студия", hint: "светлый фон, мягкий свет" },
  { id: "counter", label: "На стойке", hint: "кофейня за кадром" },
  { id: "wood", label: "На дереве", hint: "стол, дневной свет" },
  { id: "dark", label: "Тёмный фон", hint: "контровой свет" },
];

const RATIOS: { id: string; label: string; hint: string }[] = [
  { id: "1:1", label: "Квадрат", hint: "карточки меню" },
  { id: "4:3", label: "Шире", hint: "широкие карточки" },
  { id: "3:4", label: "Выше", hint: "лента во весь экран" },
];

export default function PhotoStyle({ onClose }: { onClose: () => void }) {
  const notify = useToast();
  const [site, setSite] = useState<MenuImageSettings | null>(null);
  const [prompt, setPrompt] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    get<MenuImageSettings>("/menu-images/settings/")
      .then((s) => {
        setSite(s);
        setPrompt(s.extra_prompt);
      })
      .catch((e) =>
        notify(e instanceof ApiError ? e.message : "Не удалось загрузить", "bad")
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save(body: Partial<MenuImageSettings>) {
    if (!site) return;
    setSaving(true);
    try {
      const next = await patch<MenuImageSettings>("/menu-images/settings/", body);
      setSite(next);
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  /** Картинка едет multipart-ом — с файлом иначе никак. */
  async function upload(field: "background" | "sample_photo", file: File | null) {
    if (!file) return;
    setSaving(true);
    try {
      const form = new FormData();
      form.append(field, file);
      setSite(await patchForm<MenuImageSettings>("/menu-images/settings/", form));
      notify("Загружено", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось загрузить", "bad");
    } finally {
      setSaving(false);
    }
  }

  async function drop(field: "background" | "sample_photo") {
    await save({ [field]: null } as Partial<MenuImageSettings>);
  }

  const picture = (
    field: "background" | "sample_photo",
    title: string,
    hint: string
  ) => (
    <div className="field">
      <span className="label">{title}</span>
      <div className="wrap" style={{ alignItems: "center" }}>
        {site?.[field] && (
          <img
            src={site[field] as string}
            alt=""
            style={{ width: 56, height: 56, borderRadius: 10, objectFit: "cover" }}
          />
        )}
        <input
          type="file"
          accept="image/*"
          className="input grow"
          disabled={saving}
          onChange={(e) => upload(field, e.target.files?.[0] ?? null)}
        />
        {site?.[field] && (
          <button
            className="icon-btn danger"
            aria-label="Убрать"
            title="Убрать"
            onClick={() => drop(field)}
          >
            <Icon name="trash" size={16} />
          </button>
        )}
      </div>
      <p className="muted sm mt-1 m-0">{hint}</p>
    </div>
  );

  return (
    <Modal
      onClose={onClose}
      head={
        <div className="between">
          <strong className="title lg">Как выглядят наши фото</strong>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" size={18} />
          </button>
        </div>
      }
    >
      <p className="muted sm m-0">
        Задаётся один раз и работает на каждой генерации — чтобы меню
        выглядело одной съёмкой, а не сборной солянкой.
      </p>

      <div className="field">
        <span className="label">Стиль кадра</span>
        <div className="wrap">
          {STYLES.map((s) => (
            <button
              key={s.id}
              className={"btn sm" + (site?.style === s.id ? "" : " ghost")}
              disabled={saving}
              title={s.hint}
              onClick={() => save({ style: s.id })}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <span className="label">Пропорции</span>
        <div className="wrap">
          {RATIOS.map((r) => (
            <button
              key={r.id}
              className={"btn sm" + (site?.aspect_ratio === r.id ? "" : " ghost")}
              disabled={saving}
              title={r.hint}
              onClick={() => save({ aspect_ratio: r.id })}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {picture(
        "background",
        "Свой фон",
        "Фото вашей стойки или стола — блюда встанут на него. Не загрузили — возьмём фон из выбранного стиля."
      )}

      {picture(
        "sample_photo",
        "Эталон съёмки",
        "Один удачный кадр вашего блюда. С него нейросеть возьмёт свет, цвет и ракурс — но не само блюдо: рисовать она будет то, что вы выберете."
      )}

      <label className="field">
        <span className="label">Дописка к каждому запросу</span>
        <textarea
          className="input"
          rows={2}
          value={prompt}
          maxLength={500}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Наши фирменные зелёные салфетки, вид три четверти"
        />
      </label>
      <button
        className="btn sm"
        disabled={saving || prompt === site?.extra_prompt}
        onClick={() => save({ extra_prompt: prompt.trim() })}
      >
        <Icon name="check" size={15} /> Сохранить дописку
      </button>

      <p className="muted sm mt-3">
        Каждая генерация стоит денег, поэтому стиль лучше подобрать на одном
        блюде, а уже потом запускать категорию целиком.
      </p>
    </Modal>
  );
}
