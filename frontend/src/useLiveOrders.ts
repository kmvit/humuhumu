import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";
import type { Order } from "./types";
import { playChime } from "./sound";

// Живая доска заказов: опрашивает `path` по интервалу. При появлении новых
// заказов (по id) проигрывает сигнал (если sound=true) и подсвечивает их.
//
// `alertWhen` отвечает на вопрос «этот заказ уже пора брать в работу?».
// Нужен из-за предоплаты на стойке: заказ появляется в списке, когда
// гость его оформил, а к кофемашине идти в момент оплаты. Без фильтра
// планшет пикал бы на заказ без денег и молчал бы, когда деньги пришли.
export function useLiveOrders(
  path: string,
  opts: {
    intervalMs?: number;
    sound?: boolean;
    alertWhen?: (order: Order) => boolean;
  } = {}
) {
  const { intervalMs = 5000, sound = true, alertWhen } = opts;
  const [orders, setOrders] = useState<Order[]>([]);
  const [highlight, setHighlight] = useState<Set<number>>(new Set());
  const seen = useRef<Set<number>>(new Set());
  const firstLoad = useRef(true);

  const reload = useCallback(async () => {
    let data: Order[];
    try {
      data = await get<Order[]>(path);
    } catch {
      return;
    }
    // Заказ, который ещё не пора готовить, в «видели» не заносим — иначе
    // он перестанет быть новым к тому моменту, когда станет нужным.
    const ready = data.filter((o) => (alertWhen ? alertWhen(o) : true));
    const fresh = ready.filter((o) => !seen.current.has(o.id)).map((o) => o.id);
    ready.forEach((o) => seen.current.add(o.id));
    // на первой загрузке не сигналим — иначе пикнет на все текущие заказы
    if (!firstLoad.current && fresh.length) {
      if (sound) playChime();
      setHighlight((h) => new Set([...h, ...fresh]));
      window.setTimeout(() => {
        setHighlight((h) => {
          const n = new Set(h);
          fresh.forEach((id) => n.delete(id));
          return n;
        });
      }, 6000);
    }
    firstLoad.current = false;
    setOrders(data);
  }, [path, sound, alertWhen]);

  useEffect(() => {
    reload();
    const t = window.setInterval(reload, intervalMs);
    return () => window.clearInterval(t);
  }, [reload, intervalMs]);

  return { orders, setOrders, highlight, reload };
}
