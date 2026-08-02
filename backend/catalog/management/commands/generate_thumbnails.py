from django.core.management.base import BaseCommand

from catalog.models import Product


class Command(BaseCommand):
    help = "Сгенерировать превью для товаров с изображением (по умолчанию — где превью нет)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true", help="Перегенерировать даже существующие превью"
        )

    def handle(self, *args, **options):
        qs = Product.objects.exclude(image="").exclude(image__isnull=True)
        done = 0
        for p in qs:
            if p.thumbnail and not options["force"]:
                continue
            p.make_thumbnail()
            p.save(update_fields=["thumbnail"])
            done += 1
            self.stdout.write(f"  {p.name}")
        self.stdout.write(self.style.SUCCESS(f"Готово: {done} превью"))
