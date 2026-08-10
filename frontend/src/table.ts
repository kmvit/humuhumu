// Стол гостя: берётся из QR (?table=N) и запоминается, чтобы подставляться
// в заказ. Но помним его лишь ограниченное время — иначе при повторном
// заходе (из истории браузера / установленного PWA, без нового скана)
// заказ улетал бы на старый стол.
const TABLE_KEY = "humu_table";
const TABLE_TTL_MS = 3 * 60 * 60 * 1000; // 3 часа — на время визита, не до следующего

function saveTable(table: string) {
  localStorage.setItem(TABLE_KEY, JSON.stringify({ v: table, t: Date.now() }));
}

function readTable(): string | null {
  const raw = localStorage.getItem(TABLE_KEY);
  if (!raw) return null;
  try {
    const { v, t } = JSON.parse(raw);
    if (typeof v === "string" && typeof t === "number" && Date.now() - t < TABLE_TTL_MS) {
      return v;
    }
  } catch {
    /* старый формат или мусор — считаем протухшим */
  }
  localStorage.removeItem(TABLE_KEY);
  return null;
}

// Стол из QR на столе (?table=N). Свежий скан всегда обновляет стол и срок;
// без параметра — берём сохранённый, пока не истёк TTL.
export function initTable(): string | null {
  const fromUrl = new URLSearchParams(window.location.search).get("table");
  if (fromUrl) {
    saveTable(fromUrl);
    return fromUrl;
  }
  return readTable();
}

export function clearTable() {
  localStorage.removeItem(TABLE_KEY);
}
