/** Корзина с опциями.
 *
 *  Одного и того же объёма в заказе может быть несколько строк: латте на
 *  коровьем и латте на овсяном — разные позиции с разной ценой. Поэтому
 *  ключом служит вариант ВМЕСТЕ с набором выбранных опций, а не один
 *  вариант, как было до модификаторов.
 */
import type { Product, ProductVariant } from "./types";

export type CartLine = {
  variant: number;
  /** id выбранных опций; порядок неважен — ключ его нормализует. */
  modifiers: number[];
  qty: number;
};

export type Cart = Record<string, CartLine>;

/** «12|3,7» — вариант и опции. Сортируем, иначе один набор дал бы два ключа. */
export function lineKey(variant: number, modifiers: number[]): string {
  return `${variant}|${[...modifiers].sort((a, b) => a - b).join(",")}`;
}

export function addLine(cart: Cart, variant: number, modifiers: number[] = []): Cart {
  const key = lineKey(variant, modifiers);
  const line = cart[key];
  return { ...cart, [key]: { variant, modifiers, qty: (line?.qty ?? 0) + 1 } };
}

export function removeLine(cart: Cart, key: string): Cart {
  const line = cart[key];
  if (!line) return cart;
  if (line.qty <= 1) {
    const next = { ...cart };
    delete next[key];
    return next;
  }
  return { ...cart, [key]: { ...line, qty: line.qty - 1 } };
}

export const cartCount = (cart: Cart) =>
  Object.values(cart).reduce((n, l) => n + l.qty, 0);

/** Сколько всего этого варианта в корзине — по всем наборам опций. */
export const variantCount = (cart: Cart, variant: number) =>
  Object.values(cart).reduce((n, l) => (l.variant === variant ? n + l.qty : n), 0);

/** Что уйдёт на сервер. Цены он пересчитает сам — клиенту их не доверяем. */
export const toPayload = (cart: Cart) =>
  Object.values(cart).map((l) => ({
    variant: l.variant,
    quantity: l.qty,
    ...(l.modifiers.length ? { modifiers: l.modifiers } : {}),
  }));

/** Блюду нужен выбор, если есть опции или больше одного объёма. */
export const needsPicking = (p: Product) =>
  p.variants.length > 1 || p.modifier_groups.length > 0;

/** Обязательные наборы, в которых ещё ничего не выбрано. */
export function missingRequired(p: Product, chosen: number[]): string[] {
  return p.modifier_groups
    .filter(
      (g) =>
        g.min_choices > 0 &&
        g.modifiers.filter((m) => chosen.includes(m.id)).length < g.min_choices
    )
    .map((g) => g.name);
}

/** Цена порции: вариант плюс надбавки выбранных опций. */
export function linePrice(
  variant: ProductVariant | undefined,
  groups: Product["modifier_groups"],
  chosen: number[]
): number {
  const base = Number(variant?.price ?? 0);
  const extra = groups
    .flatMap((g) => g.modifiers)
    .filter((m) => chosen.includes(m.id))
    .reduce((s, m) => s + Number(m.price_delta), 0);
  return base + extra;
}

/** «0,5 л · овсяное, без сиропа» — подпись строки в корзине и в заказе. */
export function lineCaption(
  variant: ProductVariant | undefined,
  groups: Product["modifier_groups"],
  chosen: number[]
): string {
  const names = groups
    .flatMap((g) => g.modifiers)
    .filter((m) => chosen.includes(m.id))
    .map((m) => m.name);
  return [variant?.label, names.join(", ")].filter(Boolean).join(" · ");
}
