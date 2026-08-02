// Утилиты для отображения длительности этапов заказа.
export function minutesBetween(from: string, to?: string | null): number {
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : Date.now();
  return Math.max(0, Math.round((end - start) / 60000));
}

export function fmtDuration(mins: number): string {
  if (mins < 1) return "меньше мин";
  if (mins < 60) return `${mins} мин`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h} ч ${m} мин` : `${h} ч`;
}
