import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, del, get, patch, post, postForm } from "../../api";
import Icon from "../../components/Icon";
import Modal from "../../components/ui/Modal";
import { useToast } from "../../components/ui/Toast";
import type {
  DishwareSample,
  ImageBatch,
  ImageGeneration,
  ImageQuota,
  MenuImageSettings,
  PhotoStyle,
  Product,
} from "../../types";

/** Фото блюд нейросетью: выбор посуды и стиля, запуск пачки, разбор результата.

    Сгенерированное НЕ попадает в меню само: владелец смотрит кадры и ставит
    тот, что похож на его блюдо. Нейросеть иногда рисует не тот напиток, и
    молча подменить фото значило бы показать гостю то, чего он не получит.
*/

const STYLES: { id: PhotoStyle; label: string }[] = [
  { id: "studio", label: "Студия" },
  { id: "counter", label: "На стойке" },
  { id: "wood", label: "На дереве" },
  { id: "dark", label: "Тёмный фон" },
];

/** Как часто спрашиваем сервер, дорисовалась ли пачка. */
const POLL_MS = 3000;

export default function PhotoStudio({
  title,
  products,
  onClose,
  onApplied,
}: {
  title: string;
  /** Блюда, к которым рисуем фото: одно из карточки или вся категория. */
  products: Product[];
  onClose: () => void;
  /** Фото встало в меню — каталогу пора перечитать товары. */
  onApplied: () => void;
}) {
  const notify = useToast();
  const single = products.length === 1;

  const [dishware, setDishware] = useState<DishwareSample[]>([]);
  const [settings, setSettings] = useState<MenuImageSettings | null>(null);
  const [quota, setQuota] = useState<ImageQuota | null>(null);
  const [drafts, setDrafts] = useState<ImageGeneration[]>([]);
  const [batch, setBatch] = useState<ImageBatch | null>(null);

  // По умолчанию отмечаем блюда без фото: обычный случай — «заведи меню».
  // Если фото есть у всех, отмечаем всё — значит, их хотят переснять.
  const [picked, setPicked] = useState<Set<number>>(() => {
    const empty = products.filter((p) => !p.image);
    return new Set((empty.length ? empty : products).map((p) => p.id));
  });
  const [pickedWare, setPickedWare] = useState<Set<number>>(new Set());
  const [extra, setExtra] = useState("");
  const [remember, setRemember] = useState(false);
  const [variants, setVariants] = useState(1);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [wareName, setWareName] = useState("");
  const [wareFile, setWareFile] = useState<File | null>(null);

  const total = picked.size * variants;
  const left = quota?.left ?? 0;

  const loadQuota = useCallback(async () => {
    try {
      setQuota(await get<ImageQuota>("/image-batches/quota/"));
    } catch {
      /* остаток — подсказка, без неё можно жить */
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [ware, site] = await Promise.all([
          get<DishwareSample[]>("/dishware/"),
          get<MenuImageSettings>("/menu-images/settings/"),
        ]);
        setDishware(ware.filter((d) => d.is_active));
        setSettings(site);
        if (single) {
          setDrafts(
            await get<ImageGeneration[]>(`/image-generations/?product=${products[0].id}`)
          );
        }
      } catch (e) {
        notify(e instanceof ApiError ? e.message : "Не удалось открыть студию", "bad");
      }
      loadQuota();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ——— опрос пачки, пока она рисуется ———
  const timer = useRef<number | null>(null);
  useEffect(() => {
    if (!batch || batch.counts.pending === 0) return;
    timer.current = window.setTimeout(async () => {
      try {
        setBatch(await get<ImageBatch>(`/image-batches/${batch.id}/`));
      } catch {
        /* сеть моргнула — попробуем на следующем круге */
      }
    }, POLL_MS);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [batch]);

  useEffect(() => {
    if (batch && batch.counts.pending === 0 && running) {
      setRunning(false);
      loadQuota();
    }
  }, [batch, running, loadQuota]);

  const toggle = (set: Set<number>, id: number) => {
    const next = new Set(set);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  };

  async function saveStyle(style: PhotoStyle) {
    if (!settings) return;
    setSettings({ ...settings, style });
    try {
      await patch("/menu-images/settings/", { style });
    } catch {
      notify("Стиль не сохранился — попробуйте ещё раз", "bad");
    }
  }

  async function addDishware() {
    if (!wareName.trim() || !wareFile) return notify("Нужны название и фото", "bad");
    const form = new FormData();
    form.append("name", wareName.trim());
    form.append("image", wareFile);
    try {
      const created = await postForm<DishwareSample>("/dishware/", form);
      setDishware((list) => [...list, created]);
      setPickedWare((s) => new Set(s).add(created.id));
      setWareName("");
      setWareFile(null);
      setAdding(false);
      notify("Посуда добавлена", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось загрузить", "bad");
    }
  }

  async function generate() {
    if (!picked.size) return notify("Выберите хотя бы одно блюдо", "bad");
    setRunning(true);
    try {
      if (remember && extra.trim() && settings) {
        await patch("/menu-images/settings/", { extra_prompt: extra.trim() });
        setSettings({ ...settings, extra_prompt: extra.trim() });
      }
      const created = await post<ImageBatch>("/image-batches/", {
        products: [...picked],
        variants,
        dishware: [...pickedWare],
        extra: remember ? "" : extra.trim(),
      });
      setBatch(created);
      loadQuota();
    } catch (e) {
      setRunning(false);
      notify(e instanceof ApiError ? e.message : "Не удалось запустить", "bad");
    }
  }

  async function applyOne(gen: ImageGeneration) {
    setBusy(gen.id);
    try {
      await post(`/image-generations/${gen.id}/apply/`, {});
      notify("Фото в меню", "ok");
      onApplied();
      if (batch) setBatch(await get<ImageBatch>(`/image-batches/${batch.id}/`));
      if (single)
        setDrafts(
          await get<ImageGeneration[]>(`/image-generations/?product=${products[0].id}`)
        );
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось поставить", "bad");
    } finally {
      setBusy(null);
    }
  }

  async function applyAll() {
    if (!batch) return;
    setBusy(-1);
    try {
      const res = await post<{ applied: number }>(
        `/image-batches/${batch.id}/apply_all/`,
        {}
      );
      notify(`Поставлено фото: ${res.applied}`, "ok");
      onApplied();
      setBatch(await get<ImageBatch>(`/image-batches/${batch.id}/`));
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось применить", "bad");
    } finally {
      setBusy(null);
    }
  }

  async function retry(gen: ImageGeneration) {
    setBusy(gen.id);
    try {
      await post(`/image-generations/${gen.id}/retry/`, {});
      notify("Рисуем ещё вариант", "ok");
      setRunning(true);
      if (batch) setBatch(await get<ImageBatch>(`/image-batches/${batch.id}/`));
      loadQuota();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось повторить", "bad");
    } finally {
      setBusy(null);
    }
  }

  async function drop(gen: ImageGeneration) {
    setBusy(gen.id);
    try {
      await del(`/image-generations/${gen.id}/`);
      setDrafts((list) => list.filter((g) => g.id !== gen.id));
      if (batch)
        setBatch({
          ...batch,
          generations: batch.generations.filter((g) => g.id !== gen.id),
          counts: { ...batch.counts, total: batch.counts.total - 1 },
        });
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось убрать", "bad");
    } finally {
      setBusy(null);
    }
  }

  // Показываем и свежую пачку, и прежние черновики блюда: владелец может
  // вернуться к кадру, снятому на прошлой неделе.
  const gallery = batch ? batch.generations : drafts;

  return (
    <Modal
      onClose={onClose}
      head={
        <div className="between">
          <strong className="title lg">
            <Icon name="spark" size={18} /> Фото нейросетью
          </strong>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" size={18} />
          </button>
        </div>
      }
    >
      <p className="muted sm m-0">{title}</p>
      {quota && (
        <p className="muted sm mt-1">
          Осталось {quota.left} генераций из {quota.limit} в этом месяце.
        </p>
      )}

      {!single && (
        <div className="field">
          <span className="label">Блюда</span>
          <div className="wrap">
            {products.map((p) => (
              <button
                key={p.id}
                className={"btn sm" + (picked.has(p.id) ? "" : " ghost")}
                onClick={() => setPicked((s) => toggle(s, p.id))}
              >
                {picked.has(p.id) && <Icon name="check" size={14} />} {p.name}
                {p.image && <span className="badge mini ml-1">есть фото</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="field">
        <span className="label">Наша посуда</span>
        <div className="wrap">
          {dishware.map((d) => (
            <button
              key={d.id}
              className={"btn sm" + (pickedWare.has(d.id) ? "" : " ghost")}
              onClick={() => setPickedWare((s) => toggle(s, d.id))}
              title={d.note || d.name}
            >
              <img
                src={d.image}
                alt=""
                style={{ width: 20, height: 20, borderRadius: 4, objectFit: "cover" }}
              />
              {d.name}
            </button>
          ))}
          <button className="btn sm ghost" onClick={() => setAdding((v) => !v)}>
            <Icon name="plus" size={15} /> Добавить
          </button>
        </div>
        <p className="muted sm mt-1 m-0">
          Фото вашего стакана или тарелки — блюдо нарисуется в нём. Не выбрали —
          подставим посуду категории.
        </p>
        {adding && (
          <div className="wrap mt-2" style={{ alignItems: "center" }}>
            <input
              className="input grow"
              placeholder="Стакан 0,4"
              value={wareName}
              maxLength={100}
              onChange={(e) => setWareName(e.target.value)}
            />
            <input
              type="file"
              accept="image/*"
              className="input grow"
              onChange={(e) => setWareFile(e.target.files?.[0] ?? null)}
            />
            <button className="btn sm" onClick={addDishware}>
              <Icon name="check" size={15} /> Загрузить
            </button>
          </div>
        )}
      </div>

      <div className="field">
        <span className="label">Стиль</span>
        <div className="wrap">
          {STYLES.map((s) => (
            <button
              key={s.id}
              className={"btn sm" + (settings?.style === s.id ? "" : " ghost")}
              onClick={() => saveStyle(s.id)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <p className="muted sm mt-1 m-0">
          Стиль общий на всё меню — так карточки выглядят как одна съёмка.
        </p>
      </div>

      <label className="field">
        <span className="label">Уточнение</span>
        <textarea
          className="input"
          rows={2}
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
          placeholder="Со льдом, трубочка, вид сбоку"
        />
      </label>
      <button
        className={"btn sm" + (remember ? "" : " ghost")}
        onClick={() => setRemember((v) => !v)}
      >
        <Icon name={remember ? "check" : "plus"} size={15} /> Запомнить для всех фото
      </button>

      <div className="field mt-3">
        <span className="label">Вариантов на блюдо</span>
        <div className="wrap">
          {[1, 2, 3].map((n) => (
            <button
              key={n}
              className={"btn sm" + (variants === n ? "" : " ghost")}
              onClick={() => setVariants(n)}
            >
              {n}
            </button>
          ))}
        </div>
        <p className="muted sm mt-1 m-0">
          Каждый вариант — отдельная генерация: будет из чего выбрать, но и
          спишется столько же.
        </p>
      </div>

      <button
        className="btn block mt-3"
        disabled={running || !picked.size || (quota != null && total > left)}
        onClick={generate}
      >
        <Icon name="spark" size={17} />
        {running ? "Рисуем…" : `Сгенерировать ${total} фото`}
      </button>
      {quota != null && total > left && (
        <p className="muted sm mt-2 m-0">
          В запросе {total} картинок, а в месяце осталось {left}. Выберите
          меньше блюд или напишите в «Падачу» за дополнительным пакетом.
        </p>
      )}

      {batch && batch.counts.pending > 0 && (
        <p className="muted sm mt-2">
          Готово {batch.counts.ready} из {batch.counts.total} — можно не ждать,
          рисуется в фоне.
        </p>
      )}

      {gallery.length > 0 && (
        <div className="field mt-3">
          <div className="between">
            <span className="label">Что нарисовалось</span>
            {batch && batch.counts.ready > 1 && (
              <button
                className="btn sm ghost"
                disabled={busy === -1}
                onClick={applyAll}
              >
                <Icon name="check" size={15} /> Применить все
              </button>
            )}
          </div>
          <div className="grid cols-2">
            {gallery.map((g) => (
              <div key={g.id} className="stack tight">
                {g.image ? (
                  <img
                    src={g.image}
                    alt={g.product_name}
                    style={{
                      width: "100%",
                      aspectRatio: "1 / 1",
                      objectFit: "cover",
                      borderRadius: 10,
                    }}
                  />
                ) : (
                  <div
                    className="muted sm"
                    style={{
                      display: "grid",
                      placeItems: "center",
                      aspectRatio: "1 / 1",
                      border: "1px dashed var(--rule)",
                      borderRadius: 10,
                      padding: 8,
                      textAlign: "center",
                    }}
                  >
                    {g.status === "failed" ? g.error || "Ошибка" : "Рисуется…"}
                  </div>
                )}
                <strong className="sm">
                  {g.product_name}
                  {g.applied_at && <span className="badge mini ml-1">в меню</span>}
                </strong>
                <div className="wrap">
                  {g.status === "ready" && !g.applied_at && (
                    <button
                      className="btn sm"
                      disabled={busy === g.id}
                      onClick={() => applyOne(g)}
                    >
                      <Icon name="check" size={14} /> В меню
                    </button>
                  )}
                  <button
                    className="btn sm ghost"
                    disabled={busy === g.id || g.status === "pending"}
                    onClick={() => retry(g)}
                    title="Нарисовать ещё раз тем же запросом"
                  >
                    <Icon name="spark" size={14} /> Ещё
                  </button>
                  <button
                    className="icon-btn danger"
                    aria-label="Убрать черновик"
                    disabled={busy === g.id}
                    onClick={() => drop(g)}
                  >
                    <Icon name="trash" size={15} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="muted sm mt-3">
        Нарисованное фото — иллюстрация, а не снимок вашей порции: в меню оно
        так и подписывается. Чем ближе картинка к тому, что получит гость, тем
        меньше вопросов на выдаче — поэтому и рисуем в вашей посуде.
      </p>
    </Modal>
  );
}
