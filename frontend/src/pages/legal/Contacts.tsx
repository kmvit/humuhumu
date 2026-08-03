import { useSite } from "../../site";
import { MERCHANT } from "../../legal";
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
  return (
    <LegalPage title="Реквизиты и контакты" subtitle="Сведения о продавце и способы связи.">
      <h2>Продавец</h2>
      <Row label="Наименование" value={MERCHANT.name} />
      <Row label="Юридический адрес" value={MERCHANT.address} />
      <Row label="ИНН" value={MERCHANT.inn} />
      <Row label="ОГРНИП" value={MERCHANT.ogrnip} />
      {site?.phone && <Row label="Телефон" value={site.phone} />}
      {site?.email && <Row label="E-mail" value={site.email} />}
      {site?.address && <Row label="Адрес кафе" value={site.address} />}
      {site?.working_hours && <Row label="Часы работы" value={site.working_hours} />}

      <h2>Банковские реквизиты</h2>
      <Row label="Расчётный счёт" value={MERCHANT.account} />
      <Row label="Банк" value={MERCHANT.bank} />
      <Row label="БИК банка" value={MERCHANT.bik} />
      <Row label="Корр. счёт" value={MERCHANT.corrAccount} />
      <Row label="ИНН банка" value={MERCHANT.bankInn} />
      <Row label="Адрес банка" value={MERCHANT.bankAddress} />

      <p className="muted" style={{ marginTop: 18 }}>
        По вопросам заказов, оплаты и возврата свяжитесь с нами по указанным телефону
        или электронной почте.
      </p>
    </LegalPage>
  );
}
