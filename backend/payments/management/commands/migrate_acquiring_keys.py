"""Перенос доступов к банку из переменных окружения в базу.

Разовая команда обновления. Пока установка была одна на кафе, ключи
лежали в её .env; в общей установке окружение одно на всех, и заведению
нужны свои. Команда раскладывает то, что уже прописано в окружении, по
заведениям, которые этот банк выбрали.

Осторожность здесь такая: ключи в окружении общие, а заведений много.
Если один и тот же банк выбран у двоих, отдать обоим одни и те же
доступы — значит пустить выручку одного в магазин другого. Поэтому
такой случай не угадывается, а выносится в отчёт: пусть человек решит,
чьи это ключи.

    python manage.py migrate_acquiring_keys [--dry-run] [--domain monti.padacha.ru]
"""
from django.core.management.base import BaseCommand

from core.models import Organization, SiteSettings
from core.tenancy import organization_context
from payments.acquiring import NoAcquirer, acquirer_class, _env
from payments.models import AcquiringCredentials


class Command(BaseCommand):
    help = "Перенести доступы к банку из переменных окружения в настройки заведений"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Только показать, что будет сделано",
        )
        parser.add_argument(
            "--domain", default="",
            help="Только это заведение (по домену)",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Перенести, даже если банк выбран у нескольких заведений",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        domain = options["domain"].strip()

        orgs = Organization.objects.order_by("pk")
        if domain:
            orgs = orgs.filter(domain=Organization.normalize_host(domain))
        orgs = list(orgs)
        if not orgs:
            self.stdout.write(self.style.ERROR("Заведения не найдены"))
            return

        # Кто какой банк выбрал — нужно до переноса, чтобы увидеть
        # совпадения раньше, чем ключи разъедутся по чужим заведениям.
        chosen: dict[str, list] = {}
        for org in orgs:
            with organization_context(org):
                provider = SiteSettings.load().acquiring
            if provider and provider != NoAcquirer.name:
                chosen.setdefault(provider, []).append(org)

        if not chosen:
            self.stdout.write("Ни одно заведение не выбрало банк — переносить нечего")
            return

        for provider, owners in sorted(chosen.items()):
            values = {
                f.key: _env(f.env)
                for f in acquirer_class(provider).fields
                if _env(f.env)
            }
            names = ", ".join(str(o) for o in owners)
            if not values:
                self.stdout.write(f"{provider}: в окружении пусто, пропускаем ({names})")
                continue
            if len(owners) > 1 and not options["force"]:
                self.stdout.write(self.style.WARNING(
                    f"{provider}: банк выбран у нескольких заведений ({names}) — "
                    "чьи это ключи, команда не угадывает. Разнесите вручную "
                    "или повторите с --force."
                ))
                continue

            for org in owners:
                with organization_context(org):
                    row = AcquiringCredentials.objects.filter(provider=provider).first()
                    if row and row.values():
                        self.stdout.write(f"{org}: {provider} — доступы уже заданы, не трогаем")
                        continue
                    self.stdout.write(self.style.SUCCESS(
                        f"{org}: {provider} ← {', '.join(sorted(values))}"
                        + (" (пробный запуск)" if dry else "")
                    ))
                    if dry:
                        continue
                    row = row or AcquiringCredentials(provider=provider, organization=org)
                    row.set_values(values)
                    row.save()

        if not dry:
            self.stdout.write(
                "\nГотово. Теперь эти же переменные можно убрать из .env — "
                "в общей установке они больше не читаются."
            )
