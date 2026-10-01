// Из чего сложилась выплата — словами, одинаково в «Сменах» и «Финансах».
// По этой строке выдают деньги, поэтому надбавки расписаны по отдельности:
// «бонус 1 000» без расшифровки не проверить.

const fmt = (v: string | number | null | undefined) =>
  Number(v ?? 0).toLocaleString("ru", { maximumFractionDigits: 2 });

export function payParts(p: {
  base: string;
  /** Все надбавки вместе. */
  bonus: string;
  kpi_bonus?: string | null;
  focus_bonus?: string | null;
  upsell_bonus?: string | null;
  senior_bonus?: string | null;
  penalty?: string | number | null;
  /** Оплата по часам: сколько часов к оплате и цена часа. */
  paid_hours?: string | null;
  hourly?: string | null;
}): string {
  const kpi = Number(p.kpi_bonus ?? 0);
  const focus = Number(p.focus_bonus ?? 0);
  const upsell = Number(p.upsell_bonus ?? 0);
  const senior = Number(p.senior_bonus ?? 0);
  // остаток — доля процента от выручки в оплате поровну
  const share = Number(p.bonus) - kpi - focus - upsell - senior;
  // «ставка 2 383,33 (13 ч × 183,33)» — откуда взялась сумма за время
  const perHour =
    p.paid_hours != null && p.hourly != null
      ? ` (${fmt(p.paid_hours)} ч × ${fmt(p.hourly)})`
      : "";
  const parts = [`ставка ${fmt(p.base)}${perHour}`];
  if (kpi > 0) parts.push(`КПД ${fmt(kpi)}`);
  if (focus > 0) parts.push(`фокус ${fmt(focus)}`);
  if (upsell > 0) parts.push(`допродажи ${fmt(upsell)}`);
  if (senior > 0) parts.push(`старшему ${fmt(senior)}`);
  if (share > 0.004) parts.push(`бонус ${fmt(share)}`);
  let text = parts.join(" + ");
  if (Number(p.penalty ?? 0) > 0) text += ` − списания ${fmt(p.penalty)}`;
  return text;
}
