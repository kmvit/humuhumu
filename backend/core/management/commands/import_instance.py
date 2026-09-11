"""Перенести данные отдельной установки в общую — как новое заведение.

До сих пор каждая точка жила своим инстансом со своей базой. Теперь они
съезжаются в одну, и у каждой строки появляется хозяин — заведение.
Сложность в том, что id в отдельных базах начинались с единицы: у кафе и
у Монти есть заказ №1, товар №1, пользователь №1. Поэтому перенос не
копирует строки как есть, а сдвигает все ключи на смещение, большее
любого занятого в целевой базе, и вместе с ними правит ссылки.

    # на переезжающей установке
    python manage.py dumpdata --natural-foreign --indent 1 \\
        catalog orders payments inventory shifts finance wallet loyalty users core \\
        > instance.json
    docker run ... tar czf - -C /app/media .  > media.tar.gz

    # в общей установке
    python manage.py import_instance instance.json --domain kafe.padacha.ru \\
        --name "Кафе" --media media.tar.gz

Что НЕ переносится: сами заведения и подписки (их заводит пульт-раздел),
кэш лицензии (он локальный), служебные таблицы Django — сессии, права,
типы содержимого, журнал админки. Их у общей установки свои.
"""
import json
import tarfile
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.color import no_style
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils.text import slugify

from core.models import Organization
from core.tenancy import TenantMixin, organization_context

#: Приложения, чьи данные переезжают.
TENANT_APPS = [
    "users", "catalog", "orders", "payments",
    "inventory", "shifts", "finance", "wallet", "loyalty", "core",
]

#: Не переносим: заведения и деньги «Падачи» заводятся в общей установке,
#: кэш лицензии — величина локальная.
SKIP_MODELS = {"core.organization", "core.licensestate"}


class Command(BaseCommand):
    help = "Импорт данных отдельной установки в общую как нового заведения"

    def add_arguments(self, parser):
        parser.add_argument("path", help="JSON-дамп переезжающей установки")
        parser.add_argument("--domain", required=True, help="Домен заведения")
        parser.add_argument("--name", default="", help="Название; по умолчанию из настроек дампа")
        parser.add_argument("--slug", default="")
        parser.add_argument("--media", default="", help="tar.gz тома media")
        parser.add_argument("--dry-run", action="store_true")

    # ── разбор дампа ─────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        try:
            rows = json.load(open(opts["path"], encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Не читается дамп: {exc}")

        rows = [r for r in rows if self._is_importable(r["model"])]
        if not rows:
            raise CommandError("В дампе нет данных переносимых приложений")

        by_model = {}
        for r in rows:
            by_model[r["model"]] = by_model.get(r["model"], 0) + 1
        self.stdout.write(f"К переносу {len(rows)} записей в {len(by_model)} моделях")

        domain = Organization.normalize_host(opts["domain"])
        name = opts["name"] or self._name_from_dump(rows) or domain.split(".")[0]

        with transaction.atomic():
            org = self._target_org(domain, name, opts["slug"])
            offset = self._offset()
            self.stdout.write(f"Заведение «{org.name}» (id={org.pk}), смещение ключей {offset}")

            prepared = [self._shift(r, offset, org) for r in rows]
            prepared = [r for r in prepared if r is not None]

            tmp = Path(settings.BASE_DIR) / "_import_prepared.json"
            tmp.write_text(json.dumps(prepared, ensure_ascii=False), encoding="utf-8")
            try:
                # loaddata сам откладывает проверку связей до конца загрузки,
                # поэтому порядок моделей в дампе значения не имеет.
                call_command("loaddata", str(tmp), verbosity=0)
            finally:
                tmp.unlink(missing_ok=True)

            self._reset_sequences()
            if opts["media"]:
                self._move_media(opts["media"], org)

            if opts["dry_run"]:
                self.stdout.write(self.style.WARNING("Сухой прогон — откат"))
                transaction.set_rollback(True)
                return

        self._report(org)

    def _is_importable(self, label: str) -> bool:
        label = label.lower()
        if label in SKIP_MODELS:
            return False
        app = label.split(".")[0]
        if app not in TENANT_APPS:
            return False
        try:
            model = django_apps.get_model(label)
        except LookupError:
            return False
        # Переносим только то, что принадлежит заведению. Всё прочее в этих
        # приложениях — общее для установки.
        return issubclass(model, TenantMixin)

    def _name_from_dump(self, rows) -> str:
        for r in rows:
            if r["model"].lower() == "core.sitesettings":
                return (r["fields"].get("name") or "").strip()
        return ""

    # ── целевое заведение и смещение ─────────────────────────────────────
    def _target_org(self, domain, name, slug) -> Organization:
        org = Organization.objects.filter(domain=domain).first()
        if org is not None:
            if self._has_data(org):
                raise CommandError(
                    f"В заведении «{org.name}» уже есть данные. Повторный "
                    "импорт удвоил бы их — сначала очистите заведение."
                )
            return org
        base = slug or slugify(name, allow_unicode=False) or domain.split(".")[0]
        unique, n = base[:60], 2
        while Organization.objects.filter(slug=unique).exists():
            unique, n = f"{base[:56]}-{n}", n + 1
        return Organization.objects.create(name=name, slug=unique, domain=domain)

    @staticmethod
    def _has_data(org: Organization) -> bool:
        """Есть ли в заведении хоть что-то. Защита от повторного импорта:
        он не обновляет, а добавляет — вышли бы два комплекта данных."""
        from catalog.models import Product
        from orders.models import Order

        return (
            Order.all_objects.filter(organization=org).exists()
            or Product.all_objects.filter(organization=org).exists()
        )

    def _offset(self) -> int:
        """Смещение больше любого занятого ключа во всех переносимых таблицах."""
        top = 0
        for model in django_apps.get_models():
            if not issubclass(model, TenantMixin):
                continue
            manager = getattr(model, "all_objects", model._default_manager)
            last = manager.order_by("-pk").values_list("pk", flat=True).first()
            if isinstance(last, int):
                top = max(top, last)
        # Круглое число: в логах и в базе сразу видно, что строка переезжала.
        return ((top // 100_000) + 1) * 100_000

    # ── сдвиг ключей ─────────────────────────────────────────────────────
    def _shift(self, row: dict, offset: int, org: Organization):
        model = django_apps.get_model(row["model"])
        out = {"model": row["model"], "fields": dict(row["fields"])}

        if isinstance(row.get("pk"), int):
            out["pk"] = row["pk"] + offset

        # Хозяина проставляем всегда, а не только когда поле есть в дампе:
        # переезжают установки СТАРШЕ тенантности, у них такого столбца
        # ещё не было.
        out["fields"]["organization"] = org.pk

        for field in model._meta.get_fields():
            if not getattr(field, "is_relation", False) or not field.concrete:
                continue
            name = field.name
            if name not in out["fields"]:
                continue
            value = out["fields"][name]
            if value is None:
                continue
            related = field.related_model
            if related is Organization:
                # Хозяин строки — новое заведение, а не то, что было в дампе.
                out["fields"][name] = org.pk
            elif issubclass(related, TenantMixin) and isinstance(value, int):
                # Ссылка внутри дампа — сдвигается вместе с целью.
                out["fields"][name] = value + offset
            # Ссылки наружу (ContentType и пр.) оставляем как есть.

        for field in model._meta.many_to_many:
            name = field.name
            values = out["fields"].get(name)
            if not values or not issubclass(field.related_model, TenantMixin):
                continue
            out["fields"][name] = [
                v + offset if isinstance(v, int) else v for v in values
            ]
        return out

    def _reset_sequences(self):
        """Вернуть счётчикам правильное место.

        Строки вставлены с явными ключами выше текущего значения
        последовательности — без сброса следующая запись в таблицу
        столкнулась бы с уже занятым id.
        """
        models = [m for m in django_apps.get_models() if issubclass(m, TenantMixin)]
        statements = connection.ops.sequence_reset_sql(no_style(), models)
        with connection.cursor() as cursor:
            for sql in statements:
                cursor.execute(sql)
        self.stdout.write(f"  счётчики id сброшены: таблиц {len(models)}")

    # ── файлы ────────────────────────────────────────────────────────────
    def _move_media(self, archive: str, org: Organization):
        """Распаковать файлы в папку заведения и поправить пути в базе.

        В отдельной установке файлы лежали как «products/foo.jpg», в общей
        у каждого заведения своя папка — иначе одинаковые имена у разных
        кафе затрут друг друга.
        """
        root = Path(settings.MEDIA_ROOT) / f"org-{org.pk}"
        root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            safe = [m for m in tar.getmembers() if not m.name.startswith(("/", ".."))]
            tar.extractall(root, members=safe)
        self.stdout.write(f"  файлы распакованы в media/org-{org.pk}/")

        prefix = f"org-{org.pk}/"
        moved = 0
        with organization_context(org):
            for model in django_apps.get_models():
                if not issubclass(model, TenantMixin):
                    continue
                file_fields = [
                    f.name for f in model._meta.get_fields()
                    if getattr(f, "get_internal_type", lambda: "")() in
                    ("FileField", "ImageField")
                ]
                if not file_fields:
                    continue
                manager = getattr(model, "all_objects", model._default_manager)
                for obj in manager.filter(organization=org):
                    changed = []
                    for name in file_fields:
                        value = getattr(obj, name)
                        if value and not str(value).startswith(prefix):
                            setattr(obj, name, prefix + str(value))
                            changed.append(name)
                    if changed:
                        obj.save(update_fields=changed)
                        moved += 1
        self.stdout.write(f"  путей к файлам поправлено: {moved}")

    # ── итог ─────────────────────────────────────────────────────────────
    def _report(self, org: Organization):
        from orders.models import Order
        from catalog.models import Product
        from users.models import User

        self.stdout.write(self.style.SUCCESS(f"\nЗаведение «{org.name}» перенесено."))
        for title, model in (("заказов", Order), ("товаров", Product), ("пользователей", User)):
            # У User менеджер не подменён (на нём держится вход), all_objects нет.
            manager = getattr(model, "all_objects", model._default_manager)
            count = manager.filter(organization=org).count()
            self.stdout.write(f"  {title}: {count}")
        self.stdout.write(f"  адрес: https://{org.domain}/")
