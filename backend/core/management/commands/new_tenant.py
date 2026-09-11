"""Подключить заведение к общей установке.

В отличие от deploy/new-cafe.sh, здесь не поднимается никакой стек:
заведение — это строка в базе плюс домен, который уже ведёт на общий
сервер (wildcard *.padacha.ru). Подключение занимает секунды и не грузит
сервер сборкой.

    python manage.py new_tenant "Кофейня Компас" --domain kompas.padacha.ru \\
        --license-key <ключ из пульта> --plan max

Выдаёт логин и пароль владельца — их и отдают клиенту. Django-админку
клиенту не выдаём: людьми и настройками он управляет из панели владельца.
"""
import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from core.models import Organization, SiteSettings
from core.tenancy import organization_context
from users.models import User


class Command(BaseCommand):
    help = "Создать заведение в общей установке (без развёртывания стека)"

    def add_arguments(self, parser):
        parser.add_argument("name", help="Название заведения, как увидит гость")
        parser.add_argument("--domain", required=True, help="Напр. kompas.padacha.ru")
        parser.add_argument("--slug", default="", help="Код; по умолчанию из названия")
        parser.add_argument("--license-key", default="", help="Ключ из пульта")
        parser.add_argument(
            "--plan", default=SiteSettings.Plan.START,
            choices=[p for p, _ in SiteSettings.Plan.choices],
            help="Тариф до первой сверки с пультом",
        )
        parser.add_argument(
            "--owner", default="owner", help="Логин владельца (по умолчанию owner)"
        )
        parser.add_argument(
            "--service-mode", default=SiteSettings.ServiceMode.HALL,
            choices=[m for m, _ in SiteSettings.ServiceMode.choices],
        )

    def handle(self, *args, **options):
        domain = Organization.normalize_host(options["domain"])
        if not domain:
            raise CommandError("Домен обязателен")
        if Organization.objects.filter(domain=domain).exists():
            raise CommandError(f"Домен {domain} уже занят другим заведением")

        name = options["name"].strip()
        slug = options["slug"] or slugify(name, allow_unicode=False) or domain.split(".")[0]
        if Organization.objects.filter(slug=slug).exists():
            raise CommandError(f"Код «{slug}» занят — задайте другой через --slug")

        password = secrets.token_urlsafe(12)

        with transaction.atomic():
            org = Organization.objects.create(
                name=name,
                slug=slug[:60],
                domain=domain,
                license_key=options["license_key"],
            )
            # Настройки и владелец заводятся ВНУТРИ заведения: иначе
            # привязка возьмётся от текущего и уедет соседу.
            with organization_context(org):
                site = SiteSettings.load()
                site.name = name
                site.plan = options["plan"]
                site.service_mode = options["service_mode"]
                site.save()

                owner = User(
                    username=options["owner"],
                    role=User.Role.ADMIN,
                    organization=org,
                )
                owner.set_password(password)
                owner.save()

        self.stdout.write(self.style.SUCCESS(f"Заведение «{name}» подключено."))
        self.stdout.write(f"  адрес:  https://{domain}/")
        self.stdout.write(f"  логин:  {owner.username}")
        self.stdout.write(f"  пароль: {password}")
        self.stdout.write(f"  тариф:  {site.get_plan_display()}")
        if not options["license_key"]:
            self.stdout.write(
                self.style.WARNING(
                    "  Ключ лицензии не задан — заведение не биллится. "
                    "Для клиента заведите точку в пульте и впишите ключ."
                )
            )
        self.stdout.write(
            "\nОсталось: убедиться, что домен ведёт на этот сервер "
            "(wildcard *.padacha.ru закрывает это автоматически)."
        )
