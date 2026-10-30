import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { useSite } from "../site";
import { useTheme } from "../theme";
import { initTable } from "../table";
import Icon from "./Icon";
import MenuModeSwitch from "./MenuModeSwitch";
import DemoAccess from "./DemoAccess";

type MenuHeaderProps = {
  mode: "list" | "photos";
  overlay?: boolean;
  table?: string | null;
};

/** Одна и та же навигация гостя для меню списком и полноэкранной фотоленты. */
export default function MenuHeader({ mode, overlay = false, table }: MenuHeaderProps) {
  const site = useSite();
  const { user } = useAuth();
  const { theme, toggle } = useTheme();
  const [urlTable] = useState(initTable);
  const activeTable = table === undefined ? urlTable : table;
  const counter = site?.service_mode === "counter";
  const name = site?.name || "Кафе";

  return (
    <header className={"menu-header" + (overlay ? " menu-header-overlay" : " menu-header-page")}>
      <div className="menu-header-inner">
        <Link className="menu-header-brand" to="/" aria-label={`${name} — главная`}>
          <span className="menu-header-logo">
            {site?.logo ? <img src={site.logo} alt="" /> : <Icon name="coffee" size={18} />}
          </span>
          <strong className="menu-header-name">{name}</strong>
        </Link>

        <div className="menu-header-actions">
          {activeTable && !counter && (
            <span className="menu-header-table" aria-label={`Ваш стол номер ${activeTable}`}>
              <Icon name="store" size={14} />
              <span className="menu-header-table-label">Стол&nbsp;</span>{activeTable}
            </span>
          )}
          <MenuModeSwitch current={mode} />
          <button
            className="icon-btn menu-header-theme"
            onClick={toggle}
            aria-label={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
          >
            <Icon name={theme === "dark" ? "sun" : "moon"} size={17} />
          </button>
          {!user && site?.demo_access?.length ? <DemoAccess placement="header" accounts={site.demo_access} /> : null}
          {!user && <Link className="menu-header-login" to="/login">Войти</Link>}
        </div>
      </div>
    </header>
  );
}
