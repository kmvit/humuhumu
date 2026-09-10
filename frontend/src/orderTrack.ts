import { useCallback, useEffect, useState } from "react";
import { get, ApiError } from "./api";
import type { Order } from "./types";

/** Свой заказ гостя: токен в localStorage + опрос статуса.

    Живёт отдельно от страниц, потому что заказ отслеживают оба меню —
    и обычное, и лента. Дублировать опрос и разбор 404 в двух местах значит
    рано или поздно их развести.
*/

const TOKEN_KEY = "humu_order_token";

export const readOrderToken = () => localStorage.getItem(TOKEN_KEY);

export function useTrackedOrder() {
  const [token, setToken] = useState<string | null>(() => readOrderToken());
  const [order, setOrder] = useState<Order | null>(null);

  const forget = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setOrder(null);
  }, []);

  /** Запомнить только что оформленный заказ и начать за ним следить. */
  const track = useCallback((placed: Order) => {
    if (!placed.public_token) return;
    localStorage.setItem(TOKEN_KEY, placed.public_token);
    setToken(placed.public_token);
    setOrder(placed);
  }, []);

  const reload = useCallback(async () => {
    const t = readOrderToken();
    if (!t) return;
    try {
      setOrder(await get<Order>(`/orders/track/?token=${t}`));
    } catch {
      /* следующий опрос подхватит */
    }
  }, []);

  useEffect(() => {
    if (!token) {
      setOrder(null);
      return;
    }
    let stop = false;
    const poll = () =>
      get<Order>(`/orders/track/?token=${token}`)
        .then((o) => !stop && setOrder(o))
        .catch((e) => {
          // заказа больше нет (например, почистили базу) — забываем токен,
          // иначе гость навсегда застрянет на экране несуществующего заказа
          if (e instanceof ApiError && e.status === 404 && !stop) forget();
        });
    poll();
    const id = window.setInterval(poll, 5000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [token, forget]);

  return { token, order, track, forget, reload };
}
