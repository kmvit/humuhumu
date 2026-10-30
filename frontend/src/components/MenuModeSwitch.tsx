import { Link } from "react-router-dom";

/** Компактный переключатель между двумя гостевыми форматами меню. */
export default function MenuModeSwitch({ current }: { current: "list" | "photos" }) {
  return (
    <nav className="menu-mode-switch" aria-label="Вид меню">
      {current === "list" ? (
        <span className="selected" aria-current="page">Списком</span>
      ) : (
        <Link to="/menu">Списком</Link>
      )}
      {current === "photos" ? (
        <span className="selected" aria-current="page">Фото</span>
      ) : (
        <Link to="/reels">Фото</Link>
      )}
    </nav>
  );
}
