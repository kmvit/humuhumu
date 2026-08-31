import { useSite } from "../../site";
import { useMerchant } from "../../legal";
import LegalPage from "./LegalPage";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="legal-req">
      <span className="muted">{label}</span>
      <span>{value}</span>
    </div>
  );
}

export default function Contacts() {
  const site = useSite();
  const merchant = useMerchant();
  return (
    <LegalPage title="Реквизиты и контакты" subtitle="Сведения о продавце и способы связи.">
      <h2>Продавец</h2>
      {merchant.name && <Row label="Наименование" value={merchant.name} />}
      {merchant.address && <Row label="Юридический адрес" value={merchant.address} />}
      {merchant.inn && <Row label="ИНН" value={merchant.inn} />}
      {merchant.ogrnip && <Row label="ОГРНИП" value={merchant.ogrnip} />}
      {site?.phone && <Row label="Телефон" value={site.phone} />}
      {site?.email && <Row label="E-mail" value={site.email} />}
      {site?.address && <Row label="Адрес кафе" value={site.address} />}
      {site?.working_hours && <Row label="Часы работы" value={site.working_hours} />}

      <h2>Банковские реквизиты</h2>
      {merchant.account && <Row label="Расчётный счёт" value={merchant.account} />}
      {merchant.bank && <Row label="Банк" value={merchant.bank} />}
      {merchant.bik && <Row label="БИК банка" value={merchant.bik} />}
      {merchant.corrAccount && <Row label="Корр. счёт" value={merchant.corrAccount} />}
      {merchant.bankInn && <Row label="ИНН банка" value={merchant.bankInn} />}
      {merchant.bankAddress && <Row label="Адрес банка" value={merchant.bankAddress} />}

      <p className="muted" style={{ marginTop: 18 }}>
        По вопросам заказов, оплаты и возврата свяжитесь с нами по указанным телефону
        или электронной почте.
      </p>
    </LegalPage>
  );
}
