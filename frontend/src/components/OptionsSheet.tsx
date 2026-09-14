/** Выбор объёма и опций перед добавлением блюда в заказ.
 *
 *  Порядок как у Toast: сначала объём, потом опции поверх него. Открывается
 *  только когда есть что выбирать — блюдо с одним объёмом и без опций
 *  добавляется одним тапом, как раньше: в час пик лишний экран дороже
 *  единообразия.
 */
import { useMemo, useState } from "react";
import Icon from "./Icon";
import Modal from "./ui/Modal";
import { linePrice, missingRequired } from "../cart";
import type { Product } from "../types";

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
  const [variant, setVariant] = useState<number>(sizes[0]?.id);
  const [chosen, setChosen] = useState<number[]>([]);
  const [touched, setTouched] = useState(false);

  const picked = sizes.find((v) => v.id === variant);
  const missing = missingRequired(product, chosen);
  const price = linePrice(picked, product.modifier_groups, chosen);

  const groups = useMemo(
    () => [...product.modifier_groups].sort((a, b) => a.sort_order - b.sort_order),
    [product.modifier_groups]
  );

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

  function submit() {
    setTouched(true);
    if (missing.length || !picked) return;
    onAdd(picked.id, chosen);
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

        {groups.map((group) => {
          const single = group.max_choices === 1;
          const unmet = touched && missing.includes(group.name);
          return (
            <div className="field" key={group.id}>
              <span className={"label" + (unmet ? " label-bad" : "")}>
                {group.name}
                {group.is_required ? (
                  <span className="muted"> · выберите одно</span>
                ) : group.max_choices > 1 ? (
                  <span className="muted"> · до {group.max_choices}</span>
                ) : null}
              </span>
              <div className="stack tight">
                {group.modifiers.map((m) => {
                  const on = chosen.includes(m.id);
                  const delta = Number(m.price_delta);
                  return (
                    <button
                      key={m.id}
                      className={"option-row" + (on ? " on" : "")}
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
                        <span className="num muted">
                          {delta > 0 ? "+" : "−"}
                          {Math.abs(delta).toLocaleString("ru")} ₽
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}

        {touched && missing.length > 0 && (
          <span className="sm" style={{ color: "var(--danger)" }}>
            Выберите: {missing.join(", ")}
          </span>
        )}

        <button className="btn block" onClick={submit} disabled={!picked}>
          <Icon name="plus" size={17} /> В заказ · {price.toLocaleString("ru")} ₽
        </button>
      </div>
    </Modal>
  );
}
