"""Перенести реестр из отдельного пульта в общую установку.

Пульт был самостоятельным сервисом (Django + SQLite на padacha.ru) и
хранил клиентов, точки и платежи. Теперь это часть продукта, и данные
надо забрать один раз:

    # на сервере пульта
    python manage.py dumpdata billing --indent 2 > pult.json
    # в общей установке
    python manage.py import_pult pult.json

Что во что превращается:
- billing.client   → billing.Client (заказчик как был);
- billing.instance → core.Organization (точка: домен и КЛЮЧ ЛИЦЕНЗИИ —
  по нему внешняя установка продолжит получать лицензию) плюс
  billing.Subscription (тариф, оплачено до, грейс, своя точка);
- billing.payment  → billing.Payment.

Команда идемпотентна: точка узнаётся по домену (а если он пуст — по
ключу лицензии), повторный запуск обновляет, а не плодит. Платежи не
продлевают подписку при импорте — переносится история, а «оплачено до»
берётся из пульта как есть.
"""
import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from core.models import Organization
from billing.models import Client, Payment, Subscription


class Command(BaseCommand):
    help = "Импорт клиентов, точек и платежей из дампа пульта"

    def add_arguments(self, parser):
        parser.add_argument("path", help="Файл dumpdata с пульта (billing)")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Показать, что будет сделано, и ничего не менять",
        )

    def handle(self, *args, **options):
        try:
            rows = json.load(open(options["path"], encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Не читается дамп: {exc}")

        by_model = {}
        for row in rows:
            by_model.setdefault(row["model"], []).append(row)

        clients = by_model.get("billing.client", [])
        instances = by_model.get("billing.instance", [])
        payments = by_model.get("billing.payment", [])
        self.stdout.write(
            f"В дампе: клиентов {len(clients)}, точек {len(instances)}, "
            f"платежей {len(payments)}"
        )

        with transaction.atomic():
            client_map = self._import_clients(clients)
            sub_map = self._import_instances(instances, client_map)
            self._import_payments(payments, sub_map)
            if options["dry_run"]:
                self.stdout.write(self.style.WARNING("Сухой прогон — откат"))
                transaction.set_rollback(True)

    # ── клиенты ──────────────────────────────────────────────────────────
    def _import_clients(self, rows) -> dict:
        mapping = {}
        for row in rows:
            f = row["fields"]
            client, created = Client.objects.get_or_create(
                name=f["name"],
                defaults={
                    "contact_person": f.get("contact_person", ""),
                    "phone": f.get("phone", ""),
                    "email": f.get("email", ""),
                    "notes": f.get("notes", ""),
                },
            )
            mapping[row["pk"]] = client
            self.stdout.write(
                f"  клиент «{client.name}» — {'создан' if created else 'уже есть'}"
            )
        return mapping

    # ── точки ────────────────────────────────────────────────────────────
    def _import_instances(self, rows, client_map) -> dict:
        mapping = {}
        for row in rows:
            f = row["fields"]
            domain = Organization.normalize_host(f.get("domain", ""))
            key = f.get("license_key", "")
            title = f.get("title") or "Заведение"

            org = None
            if domain:
                org = Organization.objects.filter(domain=domain).first()
            if org is None and key:
                org = Organization.objects.filter(license_key=key).first()
            if org is None:
                org = Organization(
                    name=title,
                    slug=self._free_slug(title, domain),
                )

            org.domain = domain
            org.license_key = key
            org.client = client_map.get(f.get("client"))
            org.last_seen_at = f.get("last_seen_at") or org.last_seen_at
            org.last_version = f.get("last_version", "") or org.last_version
            org.save()

            sub, _ = Subscription.objects.update_or_create(
                organization=org,
                defaults={
                    "plan": f.get("plan", "start"),
                    "paid_until": date.fromisoformat(f["paid_until"]),
                    "grace_days": f.get("grace_days", 7),
                    "is_internal": f.get("is_internal", False),
                    "notes": f.get("notes", ""),
                },
            )
            mapping[row["pk"]] = sub
            self.stdout.write(
                f"  точка «{org.name}» → {org.domain or 'без домена'}, "
                f"тариф {sub.plan}, до {sub.paid_until}"
                + (", своя" if sub.is_internal else "")
            )
        return mapping

    def _free_slug(self, title: str, domain: str) -> str:
        base = slugify(title, allow_unicode=False) or (
            domain.split(".")[0] if domain else "cafe"
        )
        slug, n = base[:60], 2
        while Organization.objects.filter(slug=slug).exists():
            slug = f"{base[:56]}-{n}"
            n += 1
        return slug

    # ── платежи ──────────────────────────────────────────────────────────
    def _import_payments(self, rows, sub_map):
        added = 0
        for row in rows:
            f = row["fields"]
            sub = sub_map.get(f.get("instance"))
            if sub is None:
                self.stdout.write(
                    self.style.WARNING(f"  платёж {row['pk']}: точка не найдена, пропуск")
                )
                continue
            paid_at = date.fromisoformat(f["paid_at"])
            if Payment.objects.filter(
                subscription=sub, paid_at=paid_at, amount=f["amount"]
            ).exists():
                continue  # повторный запуск не дублирует
            payment = Payment(
                subscription=sub,
                amount=f["amount"],
                months=f.get("months", 1),
                paid_at=paid_at,
                comment=f.get("comment", ""),
            )
            # Платёж обычно продлевает подписку, но при импорте это было бы
            # двойным счётом: «оплачено до» уже посчитано пультом.
            Payment.objects.bulk_create([payment])
            added += 1
        self.stdout.write(f"  платежей перенесено: {added}")
