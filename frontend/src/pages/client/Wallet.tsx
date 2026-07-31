import { useEffect, useState } from "react";
import { get } from "../../api";
import type { TokenPackage, TokenTransaction, Wallet as WalletT } from "../../types";
import Icon, { type IconName } from "../../components/Icon";

const TX_ICON: Record<string, IconName> = {
  topup: "arrowUp",
  bonus: "gift",
  purchase: "coffee",
  refund: "arrowDown",
  adjustment: "spark",
};

export default function Wallet() {
  const [wallet, setWallet] = useState<WalletT | null>(null);
  const [packages, setPackages] = useState<TokenPackage[]>([]);
  const [txns, setTxns] = useState<TokenTransaction[]>([]);

  useEffect(() => {
    get<WalletT>("/wallet/").then(setWallet).catch(() => {});
    get<TokenPackage[]>("/token-packages/").then(setPackages).catch(() => {});
    get<TokenTransaction[]>("/wallet/transactions/").then(setTxns).catch(() => {});
  }, []);

  return (
    <>
      <h1 className="h1">Кошелёк</h1>

      <div className="card balance-card hover enter" style={{ marginTop: 14 }}>
        <span className="label">Баланс токенов</span>
        <div className="amount num">{wallet ? Number(wallet.balance).toLocaleString("ru") : "—"}</div>
        <span className="label" style={{ position: "relative", zIndex: 1 }}>1 токен = 1 ₽</span>
      </div>

      <h2 className="section-title">Пакеты токенов</h2>
      <p className="muted" style={{ marginTop: -6, marginBottom: 12 }}>
        Пополнение — на кассе. Покажите кассиру нужный пакет.
      </p>
      <div className="grid stagger">
        {packages.map((p) => (
          <div className="card hover" key={p.id}>
            <div className="between">
              <span className="price num">{p.pay_amount} ₽</span>
              {Number(p.bonus_amount) > 0 && (
                <span className="badge paid"><Icon name="gift" size={13} /> +{p.bonus_amount}</span>
              )}
            </div>
            <div className="muted" style={{ marginTop: 8 }}>Зачислим</div>
            <div style={{ fontFamily: "Fredoka", fontWeight: 700, fontSize: 24, color: "var(--brand)" }} className="num">
              {p.total_tokens} ток.
            </div>
          </div>
        ))}
      </div>

      <h2 className="section-title">История</h2>
      <div className="card">
        {txns.length === 0 && <p className="muted">Пока пусто.</p>}
        {txns.map((t) => {
          const pos = Number(t.amount) >= 0;
          return (
            <div className="row" key={t.id}>
              <span className="tx-icon"><Icon name={TX_ICON[t.type] ?? "spark"} size={18} /></span>
              <div className="stack" style={{ gap: 2, flex: 1 }}>
                <strong>{t.type_display}</strong>
                <span className="muted">{new Date(t.created_at).toLocaleString("ru", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
              </div>
              <span className={pos ? "amount-pos" : "amount-neg"}>
                {pos ? "+" : ""}{t.amount}
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}
