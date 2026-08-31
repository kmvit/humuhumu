// Реквизиты продавца и дата редакции документов приходят из настроек
// заведения (/api/site/ → SiteSettings). В коде их держать нельзя:
// продукт ставится разным кафе, у каждого свои ИНН, счёт и банк.
import { useSite } from "./site";
import type { Site } from "./types";

export type Merchant = {
  type: string;
  name: string;
  shortName: string;
  address: string;
  inn: string;
  ogrnip: string;
  account: string;
  bank: string;
  bankInn: string;
  bik: string;
  corrAccount: string;
  bankAddress: string;
};

const EMPTY: Merchant = {
  type: "",
  name: "",
  shortName: "",
  address: "",
  inn: "",
  ogrnip: "",
  account: "",
  bank: "",
  bankInn: "",
  bik: "",
  corrAccount: "",
  bankAddress: "",
};

function fromSite(site: Site | null): Merchant {
  if (!site) return EMPTY;
  return {
    type: site.merchant_type ?? "",
    name: site.merchant_name ?? "",
    shortName: site.merchant_short ?? "",
    address: site.merchant_address ?? "",
    inn: site.merchant_inn ?? "",
    ogrnip: site.merchant_ogrn ?? "",
    account: site.merchant_account ?? "",
    bank: site.merchant_bank ?? "",
    bankInn: site.merchant_bank_inn ?? "",
    bik: site.merchant_bik ?? "",
    corrAccount: site.merchant_corr_account ?? "",
    bankAddress: site.merchant_bank_address ?? "",
  };
}

/** Реквизиты продавца. Пока настройки не заполнены — пустые строки. */
export function useMerchant(): Merchant {
  return fromSite(useSite());
}

/** Заполнены ли реквизиты — юр. страницы без них показывают заглушку. */
export function useHasMerchant(): boolean {
  const m = useMerchant();
  return Boolean(m.name && m.inn);
}

/** Эквайер (приём онлайн-оплаты). */
export function useAcquirer(): string {
  return useSite()?.acquirer ?? "";
}

/** Дата последней редакции документов. */
export function useLegalUpdated(): string {
  return useSite()?.legal_updated ?? "";
}
