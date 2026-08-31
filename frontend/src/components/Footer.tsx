import { Link } from "react-router-dom";
import { useSite } from "../site";
import { useMerchant } from "../legal";
import Icon, { type IconName } from "./Icon";

const DOCS = [
  { to: "/offer", label: "Публичная оферта" },
  { to: "/payment", label: "Оплата и возврат" },
  { to: "/privacy", label: "Политика конфиденциальности" },
  { to: "/contacts", label: "Реквизиты и контакты" },
];

export default function Footer() {
  const site = useSite();
  const merchant = useMerchant();
  if (!site) return null;

  const contacts: { icon: IconName; text: string; href?: string }[] = [
    site.phone ? { icon: "store", text: site.phone, href: `tel:${site.phone.replace(/[^+\d]/g, "")}` } : null,
    site.email ? { icon: "receipt", text: site.email, href: `mailto:${site.email}` } : null,
    site.address ? { icon: "leaf", text: site.address } : null,
    site.working_hours ? { icon: "sun", text: site.working_hours } : null,
  ].filter(Boolean) as { icon: IconName; text: string; href?: string }[];

  return (
    <footer className="footer">
      <div className="footer-inner">
        <div className="stack" style={{ gap: 6, flex: 1, minWidth: 200 }}>
          <span className="brand" style={{ fontSize: 24 }}>
            <span className="logo">
              {site.logo ? <img src={site.logo} alt="" /> : <Icon name="coffee" size={17} />}
            </span>
            <span className="brand-name">{site.name}</span>
          </span>
          {site.tagline && (
            <span className="script" style={{ fontSize: 19, color: "var(--brand-2)" }}>{site.tagline}</span>
          )}
          {site.about && <p className="muted" style={{ margin: 0, maxWidth: 360 }}>{site.about}</p>}
        </div>

        <div className="stack" style={{ gap: 10 }}>
          {contacts.map((c) => (
            <span className="footer-item" key={c.text}>
              <span className="tx-icon" style={{ width: 30, height: 30 }}><Icon name={c.icon} size={15} /></span>
              {c.href ? <a href={c.href}>{c.text}</a> : <span>{c.text}</span>}
            </span>
          ))}
          <div className="wrap" style={{ marginTop: 4 }}>
            {site.instagram && (
              <a className="btn sm ghost" href={`https://instagram.com/${site.instagram}`} target="_blank" rel="noreferrer">
                <Icon name="flower" size={15} /> Instagram
              </a>
            )}
            {site.telegram && (
              <a className="btn sm ghost" href={`https://t.me/${site.telegram}`} target="_blank" rel="noreferrer">
                <Icon name="wave" size={15} /> Telegram
              </a>
            )}
          </div>
        </div>

        <div className="stack" style={{ gap: 8 }}>
          <span className="footer-title">Документы</span>
          {DOCS.map((d) => (
            <Link className="footer-link" key={d.to} to={d.to}>
              {d.label}
            </Link>
          ))}
        </div>
      </div>

      <p className="muted footer-copy">
        © {site.name}
        {merchant.shortName && ` · ${merchant.shortName}`}
        {merchant.inn && ` · ИНН ${merchant.inn}`}
        {merchant.ogrnip && ` · ОГРНИП ${merchant.ogrnip}`}
      </p>
    </footer>
  );
}
