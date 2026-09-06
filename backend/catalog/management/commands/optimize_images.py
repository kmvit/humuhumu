import os
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image, ImageOps

from catalog.models import Category, Product

# Reels показывает фото на весь экран, поэтому длинной стороны 1280 хватает
# даже для плотных экранов; иконки категорий — мелкие, им хватает 512.
TARGETS = {
    "products": {"model": Product, "field": "image", "max_side": 1280},
    "icons": {"model": Category, "field": "icon", "max_side": 512},
}


def human(size):
    return f"{size / 1024:.0f} КБ" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} МБ"


class Command(BaseCommand):
    help = (
        "Пережать загруженные изображения в WebP с разумным размером. "
        "Оригиналы заменяются, старые файлы удаляются."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--only", choices=sorted(TARGETS), help="Обрабатывать только эту группу"
        )
        parser.add_argument("--max-side", type=int, help="Переопределить длинную сторону, px")
        parser.add_argument("--quality", type=int, default=80, help="Качество WebP (по умолчанию 80)")
        parser.add_argument(
            "--min-kb",
            type=int,
            default=150,
            help="Не трогать файлы легче этого размера, если они уже в пределах max-side",
        )
        parser.add_argument("--force", action="store_true", help="Пережать даже уже оптимизированные")
        parser.add_argument("--dry-run", action="store_true", help="Только показать, ничего не менять")
        parser.add_argument("--keep-original", action="store_true", help="Не удалять старые файлы")

    def handle(self, *args, **opts):
        groups = [opts["only"]] if opts["only"] else list(TARGETS)
        total_before = total_after = 0
        changed = 0
        for group in groups:
            spec = TARGETS[group]
            max_side = opts["max_side"] or spec["max_side"]
            self.stdout.write(self.style.MIGRATE_HEADING(f"{group} (до {max_side}px):"))
            for obj in spec["model"].objects.exclude(**{spec["field"]: ""}).exclude(
                **{f"{spec['field']}__isnull": True}
            ):
                before, after = self.process(obj, spec["field"], max_side, opts)
                if after is None:
                    continue
                total_before += before
                total_after += after
                changed += 1
        verb = "Было бы сэкономлено" if opts["dry_run"] else "Сэкономлено"
        saved = total_before - total_after
        self.stdout.write(
            self.style.SUCCESS(
                f"Файлов: {changed}. {verb}: {human(saved)} "
                f"({human(total_before)} → {human(total_after)})"
            )
        )

    def process(self, obj, field_name, max_side, opts):
        field = getattr(obj, field_name)
        try:
            before = field.size
        except (FileNotFoundError, OSError):
            self.stdout.write(self.style.WARNING(f"  нет файла: {field.name}"))
            return 0, None
        try:
            with field.open("rb") as f:
                img = Image.open(f)
                img.load()
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"  не читается {field.name}: {exc}"))
            return 0, None

        already_webp = (img.format or "").upper() == "WEBP"
        fits = max(img.size) <= max_side
        if not opts["force"] and fits:
            # уже WebP нужного размера — повторно жать нечего: выигрыш будет
            # в проценты, а файл каждый раз получал бы новое имя
            if already_webp or before <= opts["min_kb"] * 1024:
                return 0, None

        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=opts["quality"], method=6)
        data = buf.getvalue()
        after = len(data)

        if after >= before and not opts["force"]:
            # пережимать смысла нет — оригинал и так компактнее
            return 0, None

        line = f"  {field.name}: {human(before)} → {human(after)}"
        if opts["dry_run"]:
            self.stdout.write(line)
            return before, after

        old_name = field.name
        base = os.path.splitext(os.path.basename(old_name))[0]
        field.save(f"{base}.webp", ContentFile(data), save=True)
        if not opts["keep_original"] and field.name != old_name:
            field.storage.delete(old_name)
        self.stdout.write(line)
        return before, after
