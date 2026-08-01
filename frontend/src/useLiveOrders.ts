import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";
import type { Order } from "./types";
import { playChime } from "./sound";

// Живая доска заказов: опрашивает `path` по интервалу. При появлении новых
// заказов (по id) проигрывает сигнал (если sound=true) и подсвечивает их.
export function useLiveOrders(
  path: string,
  opts: { intervalMs?: number; sound?: boolean } = {}
) {
  const { intervalMs = 5000, sound = true } = opts;
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
    const fresh = data.filter((o) => !seen.current.has(o.id)).map((o) => o.id);
    data.forEach((o) => seen.current.add(o.id));
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
  }, [path, sound]);

  useEffect(() => {
    reload();
    const t = window.setInterval(reload, intervalMs);
    return () => window.clearInterval(t);
  }, [reload, intervalMs]);

  return { orders, setOrders, highlight, reload };
}
