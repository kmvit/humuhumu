/** Выбор объёма и опций перед добавлением блюда в заказ.
 *
 *  Порядок как у Toast: сначала объём, потом опции поверх него. Открывается
 *  только когда есть что выбирать — блюдо с одним объёмом и без опций
 *  добавляется одним тапом, как раньше: в час пик лишний экран дороже
 *  единообразия.
 *
 *  Группы схлопнуты. У кофейни бывает шесть видов молока и два десятка
 *  сиропов — открытыми списками это метровая простыня, в которой не видно
 *  ни цены, ни кнопки. Свёрнутая группа — одна строка со сводкой выбранного,
 *  так что весь выбор помещается на экран целиком.
 */
import { useMemo, useState } from "react";
import Icon from "./Icon";
import Modal from "./ui/Modal";
import { linePrice, missingRequired } from "../cart";
import type { ModifierGroup, Product } from "../types";

/** Со скольких опций в группе показывать поиск. */
const SEARCH_FROM = 8;

/** «1 вариант», «2 варианта», «5 вариантов» — иначе в свёрнутой строке
 *  висит «4 вариантов». */
function variants(n: number): string {
  const teen = n % 100 >= 11 && n % 100 <= 14;
  const one = !teen && n % 10 === 1;
  const few = !teen && n % 10 >= 2 && n % 10 <= 4;
  return `${n} ${one ? "вариант" : few ? "варианта" : "вариантов"}`;
}

/** Какие группы раскрыты сразу: без выбора здесь заказ не отправить,
 *  и короткие — их сворачивать нечего, строка-заголовок съест ту же высоту. */
function opensAtStart(group: ModifierGroup): boolean {
  return group.min_choices > 0 || group.modifiers.length <= 3;
}

export default function OptionsSheet({
  product,
  onClose,
  onAdd,
}: {
  product: Product;
  onClose: () => void;
  /** Вызывается с выбранным объёмом и списком опций. */
  onAdd: (variant: number, modifiers: number[]) => void;
}) {
  const sizes = product.variants;
  const groups = useMemo(
    () => [...product.modifier_groups].sort((a, b) => a.sort_order - b.sort_order),
    [product.modifier_groups]
  );

  const [variant, setVariant] = useState<number>(sizes[0]?.id);
  const [chosen, setChosen] = useState<number[]>([]);
  const [touched, setTouched] = useState(false);
  const [open, setOpen] = useState<number[]>(() =>
    groups.filter(opensAtStart).map((g) => g.id)
  );
  // Поиск по опциям — свой у каждой группы: сиропов бывает два десятка,
  // и «виш» быстрее, чем скролл до буквы «в».
  const [query, setQuery] = useState<Record<number, string>>({});

  const picked = sizes.find((v) => v.id === variant);
  const missing = missingRequired(product, chosen);
  const price = linePrice(picked, product.modifier_groups, chosen);

  function toggle(groupId: number, modifierId: number, single: boolean) {
    setChosen((cur) => {
      if (cur.includes(modifierId)) return cur.filter((id) => id !== modifierId);
      if (!single) return [...cur, modifierId];
      // «ровно одно из набора»: прежний выбор этой же группы снимаем
      const group = groups.find((g) => g.id === groupId);
      const siblings = new Set(group?.modifiers.map((m) => m.id));
      return [...cur.filter((id) => !siblings.has(id)), modifierId];
    });
  }

  function toggleGroup(id: number) {
    setOpen((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  }

  function submit() {
    setTouched(true);
    if (!picked) return;
    if (missing.length) {
      // Незаполненную группу раскрываем: иначе гость видит красную подпись
      // на свёрнутой строке и не понимает, куда нажимать.
      setOpen((cur) => [
        ...cur,
        ...groups.filter((g) => missing.includes(g.name)).map((g) => g.id),
      ]);
      return;
    }
    onAdd(picked.id, chosen);
  }

  const money = (v: number) =>
    (v > 0 ? "+" : "−") + Math.abs(v).toLocaleString("ru") + " ₽";

  /** Что показать в свёрнутой строке группы вместо списка. */
  function summary(group: ModifierGroup) {
    const on = group.modifiers.filter((m) => chosen.includes(m.id));
    if (on.length === 0) return null;
    const extra = on.reduce((s, m) => s + Number(m.price_delta), 0);
    return on.map((m) => m.name).join(", ") + (extra ? ` · ${money(extra)}` : "");
  }

  return (
    <Modal
      onClose={onClose}
      variant="sheet"
      head={
        <div className="between">
          <strong className="title lg">{product.name}</strong>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" size={18} />
          </button>
        </div>
      }
    >
      <div className="stack loose">
        {sizes.length > 1 && (
          <div className="field">
            <span className="label">Объём</span>
            <div className="size-row" role="group" aria-label="Объём">
              {sizes.map((v) => (
                <button
                  key={v.id}
                  className={"size-chip lg" + (v.id === variant ? " active" : "")}
                  aria-pressed={v.id === variant}
                  disabled={v.is_stopped}
                  onClick={() => setVariant(v.id)}
                >
                  {v.label}
                  {v.is_stopped && <span className="size-out"> · стоп</span>}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="stack tight">
          {groups.map((group) => {
            const single = group.max_choices === 1;
            const unmet = touched && missing.includes(group.name);
            const shown = open.includes(group.id);
            const chose = summary(group);
            const q = (query[group.id] || "").trim().toLowerCase();
            const list = q
              ? group.modifiers.filter((m) => m.name.toLowerCase().includes(q))
              : group.modifiers;
            return (
              <div
                className={"opt-group" + (shown ? " open" : "") + (unmet ? " bad" : "")}
                key={group.id}
              >
                <button
                  className="opt-head"
                  aria-expanded={shown}
                  onClick={() => toggleGroup(group.id)}
                >
                  <span className="grow">
                    <span className={"label" + (unmet ? " label-bad" : "")}>
                      {group.name}
                      {group.min_choices > 0 ? (
                        <span className="muted"> · обязательно</span>
                      ) : group.max_choices > 1 ? (
                        <span className="muted"> · до {group.max_choices}</span>
                      ) : null}
                    </span>
                    {/* Сводка вместо списка: свёрнутая группа всё равно
                        показывает, что выбрано и почём. */}
                    <span className={"opt-summary" + (chose ? " on" : "")}>
                      {chose ||
                        (group.min_choices > 0
                          ? "выберите одно"
                          : variants(group.modifiers.length))}
                    </span>
                  </span>
                  <span className="opt-caret">
                    <Icon name="chevronRight" size={16} />
                  </span>
                </button>

                {shown && (
                  <div className="opt-list">
                    {group.modifiers.length >= SEARCH_FROM && (
                      <input
                        className="input opt-search"
                        value={query[group.id] || ""}
                        onChange={(e) =>
                          setQuery((cur) => ({ ...cur, [group.id]: e.target.value }))
                        }
                        placeholder="Найти…"
                        aria-label={`Поиск: ${group.name}`}
                      />
                    )}
                    {list.map((m) => {
                      const on = chosen.includes(m.id);
                      const delta = Number(m.price_delta);
                      return (
                        <button
                          key={m.id}
                          className={"opt-item" + (on ? " on" : "")}
                          aria-pressed={on}
                          disabled={m.is_stopped}
                          onClick={() => toggle(group.id, m.id, single)}
                        >
                          <span className={"option-mark" + (single ? " round" : "")}>
                            {on && <Icon name="check" size={13} />}
                          </span>
                          <span className="grow">{m.name}</span>
                          {m.is_stopped ? (
                            <span className="muted sm">стоп</span>
                          ) : delta ? (
                            <span className="num muted">{money(delta)}</span>
                          ) : null}
                        </button>
                      );
                    })}
                    {list.length === 0 && (
                      <p className="muted sm opt-empty">Ничего не нашлось</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Кнопка липнет к низу листа: со свёрнутыми группами она почти
            всегда на экране, но длинный раскрытый сироп её бы утащил. */}
        <div className="opt-actions">
          {touched && missing.length > 0 && (
            <span className="sm" style={{ color: "var(--danger)" }}>
              Выберите: {missing.join(", ")}
            </span>
          )}
          <button className="btn block" onClick={submit} disabled={!picked}>
            <Icon name="plus" size={17} /> В заказ · {price.toLocaleString("ru")} ₽
          </button>
        </div>
      </div>
    </Modal>
  );
}
