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
  senior_bonus?: string | null;
  penalty?: string | number | null;
}): string {
  const kpi = Number(p.kpi_bonus ?? 0);
  const senior = Number(p.senior_bonus ?? 0);
  // остаток — доля процента от выручки в оплате поровну
  const share = Number(p.bonus) - kpi - senior;
  const parts = [`ставка ${fmt(p.base)}`];
  if (kpi > 0) parts.push(`КПД ${fmt(kpi)}`);
  if (senior > 0) parts.push(`старшему ${fmt(senior)}`);
  if (share > 0.004) parts.push(`бонус ${fmt(share)}`);
  let text = parts.join(" + ");
  if (Number(p.penalty ?? 0) > 0) text += ` − списания ${fmt(p.penalty)}`;
  return text;
}
