import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";
import type { Order } from "./types";
import { playChime } from "./sound";

// Живая доска заказов: опрашивает сервер по интервалу, при появлении
// новых заказов проигрывает сигнал и возвращает их id для подсветки.
export function useLiveOrders(status: string, intervalMs = 5000) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [highlight, setHighlight] = useState<Set<number>>(new Set());
  const seen = useRef<Set<number>>(new Set());
  const firstLoad = useRef(true);

  const reload = useCallback(async () => {
    let data: Order[];
    try {
      data = await get<Order[]>(`/orders/?status=${status}`);
    } catch {
      return;
    }
    const fresh = data.filter((o) => !seen.current.has(o.id)).map((o) => o.id);
    data.forEach((o) => seen.current.add(o.id));
    // на самой первой загрузке не сигналим — иначе пикнет на все текущие заказы
    if (!firstLoad.current && fresh.length) {
      playChime();
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
  }, [status]);

  useEffect(() => {
    reload();
    const t = window.setInterval(reload, intervalMs);
    return () => window.clearInterval(t);
  }, [reload, intervalMs]);

  return { orders, setOrders, highlight, reload };
}
