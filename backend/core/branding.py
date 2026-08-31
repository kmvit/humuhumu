"""Манифест PWA и иконки приложения — из настроек заведения.

Продукт ставится разным кафе, поэтому имя приложения, цвета и иконка
не могут лежать в сборке фронта: иначе у всех заведений приложение
установится под одним и тем же именем. Всё берётся из SiteSettings.
"""
import io

from django.http import HttpResponse, JsonResponse
from PIL import Image, ImageDraw, ImageFont

from .models import SiteSettings

ICON_SIZES = (192, 512)
DEFAULT_ACCENT = "#1f58a6"

# Шрифт для монограммы. Встроенный в Pillow не знает кириллицы (вместо буквы
# рисуется «квадрат»), поэтому берём системный DejaVu — он ставится в образ
# пакетом fonts-dejavu-core. Если шрифта нет, монограмму не рисуем вовсе.
_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


def _mono_font(px: int):
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return None


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return _rgb(DEFAULT_ACCENT)


def _ink(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    """Чёрный или белый — что читается на этом фоне."""
    r, g, b = bg
    return (0, 0, 0) if (0.299 * r + 0.587 * g + 0.114 * b) > 160 else (255, 255, 255)


def _short_name(name: str, limit: int = 12) -> str:
    """short_name подписывает иконку на телефоне — длинное имя там обрежется."""
    name = (name or "").strip()
    if not name:
        return "Кафе"
    if len(name) <= limit:
        return name
    return (name.split()[0] if name.split() else name)[:limit]


def render_icon(site: SiteSettings, size: int, maskable: bool = False) -> bytes:
    """Иконка приложения: логотип заведения на акцентном фоне.

    Если логотипа нет — монограмма по первой букве названия, чтобы новое
    кафе не получало чужую картинку из сборки.
    """
    bg = _rgb(site.accent_color or DEFAULT_ACCENT)
    img = Image.new("RGB", (size, size), bg)

    drawn = False
    if site.logo:
        try:
            with site.logo.open("rb") as f:
                logo = Image.open(f)
                logo.load()
            logo = logo.convert("RGBA")
            # maskable обрезается системой по кругу — оставляем safe zone пошире
            box = int(size * (0.6 if maskable else 0.78))
            # Именно resize, а не thumbnail: маленький логотип нужно ещё и
            # увеличить, иначе на иконке 512 он останется точкой посередине.
            k = min(box / logo.width, box / logo.height)
            logo = logo.resize(
                (max(1, round(logo.width * k)), max(1, round(logo.height * k))),
                Image.LANCZOS,
            )
            img.paste(logo, ((size - logo.width) // 2, (size - logo.height) // 2), logo)
            drawn = True
        except Exception:  # битый или недоступный файл — не роняем иконку
            drawn = False

    if not drawn:
        draw = ImageDraw.Draw(img)
        letter = (site.name or "").strip()[:1].upper()
        # maskable система обрезает по кругу — знак должен влезть в safe zone
        font = _mono_font(int(size * (0.34 if maskable else 0.46))) if letter else None
        if font is not None:
            left, top, right, bottom = draw.textbbox((0, 0), letter, font=font)
            draw.text(
                ((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top),
                letter, font=font, fill=_ink(bg),
            )
        else:
            # Без шрифта — нейтральный знак: кольцо по центру.
            pad = size * (0.34 if maskable else 0.28)
            draw.ellipse(
                [pad, pad, size - pad, size - pad],
                outline=_ink(bg), width=max(2, int(size * 0.06)),
            )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def app_icon(request, size: int, maskable: bool = False):
    """GET /app-icon-<size>.png — иконка приложения для манифеста."""
    if size not in ICON_SIZES:
        size = ICON_SIZES[0]
    png = render_icon(SiteSettings.load(), size, maskable)
    resp = HttpResponse(png, content_type="image/png")
    # Пять минут: смена лого в админке доезжает быстро, но не бьёт по нагрузке.
    resp["Cache-Control"] = "public, max-age=300"
    return resp


def manifest(request):
    """GET /manifest.webmanifest — манифест PWA конкретного заведения."""
    site = SiteSettings.load()
    name = (site.name or "Кафе").strip()
    accent = (site.accent_color or DEFAULT_ACCENT).strip()

    icons = []
    for s in ICON_SIZES:
        icons.append(
            {"src": f"/app-icon-{s}.png", "sizes": f"{s}x{s}", "type": "image/png", "purpose": "any"}
        )
    icons.append(
        {
            "src": "/app-icon-512-maskable.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        }
    )

    data = {
        "name": name,
        # Короткое имя заведение задаёт само; пусто — аккуратно режем длинное.
        "short_name": (site.app_short_name or "").strip() or _short_name(name),
        "description": (site.tagline or "").strip() or f"Меню и заказы — {name}",
        "lang": "ru",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": accent,
        "background_color": accent,
        "icons": icons,
    }
    resp = JsonResponse(data, content_type="application/manifest+json")
    # Как index.html: не кэшируем, иначе переименование заведения не доедет.
    resp["Cache-Control"] = "no-cache"
    return resp
