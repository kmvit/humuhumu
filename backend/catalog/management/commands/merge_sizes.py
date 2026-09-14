"""Схлопнуть блюда, отличающиеся только объёмом, в одно с вариантами.

До вариантов объёмы заводились отдельными блюдами: «КИС-КИС 0.33»,
«КИС-КИС 0.5», «КИС-КИС 0.7» — три карточки в витрине, три набора лайков,
три строки в отчёте вместо разбивки одного напитка.

Переезд дешёвый: позиции заказов и тех карты ссылаются на ВАРИАНТ, а не на
товар. Значит, достаточно перевесить вариант на общее блюдо и проставить
ему метку — история заказов и составы едут следом сами, ничего не
пересчитывается.

По умолчанию — сухой прогон: печатает, что сделает, и ничего не трогает.
Применять только с --apply.
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Product, ProductLike, ProductVariant
from core.models import Organization
from core.tenancy import organization_context

#: «КИС-КИС 0.33», «КАПУЧИНО 0,2 л» → основа + объём. Хвост берём только
#: числовой: «МАТЧА-ЛАТТЕ BASE ICE» — это не объём, а другой напиток, и
#: решать за владельца, объединять ли их, команда не должна.
SIZE_RE = re.compile(
    r"^(?P<base>.+?)[\s|·-]+(?P<size>\d{1,2}(?:[.,]\d{1,3})?)\s*(?:л|l|L|Л)?$"
)


def split_name(name: str):
    """(«КИС-КИС», Decimal('0.33')) или None, если объёма в названии нет."""
    m = SIZE_RE.match(name.strip())
    if not m:
        return None
    base = m.group("base").strip(" |·-")
    try:
        size = Decimal(m.group("size").replace(",", "."))
    except InvalidOperation:
        return None
    if not base or size <= 0:
        return None
    return base, size


def label_for(size: Decimal, unit: str) -> str:
    """Decimal('0.33') → «0,33 л». Запятая — как принято в русском меню."""
    text = format(size.normalize(), "f").replace(".", ",")
    return f"{text} {unit}".strip()


class Command(BaseCommand):
    help = "Объединить блюда, отличающиеся объёмом, в одно с вариантами"

    def add_arguments(self, parser):
        parser.add_argument("--domain", required=True, help="Домен заведения")
        parser.add_argument(
            "--unit", default="л",
            help="Единица в метке варианта; пусто — только число (по умолчанию «л»)",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Применить. Без этого флага — только показать, что будет сделано",
        )

    def handle(self, *args, **opts):
        domain = Organization.normalize_host(opts["domain"])
        org = Organization.objects.filter(domain=domain).first()
        if org is None:
            raise CommandError(f"Заведение с доменом {domain} не найдено")

        with organization_context(org):
            groups = self._groups()
            if not groups:
                self.stdout.write("Блюд, отличающихся только объёмом, не нашлось.")
                return
            self._report(groups, opts["unit"])
            if not opts["apply"]:
                self.stdout.write(
                    self.style.WARNING(
                        "\nСухой прогон — ничего не изменено. "
                        "Применить: тот же вызов с --apply"
                    )
                )
                return
            with transaction.atomic():
                merged, dropped = self._merge(groups, opts["unit"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nГотово: {merged} блюд с вариантами, "
                    f"карточек в меню стало меньше на {dropped}."
                )
            )

    # ── разбор ───────────────────────────────────────────────────────────
    def _groups(self):
        """{(категория, основа): [(объём, товар), ...]} — только где ≥2 объёмов."""
        buckets: dict[tuple[int, str], list] = defaultdict(list)
        for product in Product.objects.select_related("category").prefetch_related(
            "variants"
        ):
            parsed = split_name(product.name)
            if parsed is None:
                continue
            base, size = parsed
            # Схлопываем только то, что уже переехало в один безымянный
            # вариант: блюдо, которому владелец уже завёл объёмы руками,
            # трогать нельзя — там своя разметка.
            variants = list(product.variants.all())
            if len(variants) != 1 or variants[0].label:
                continue
            buckets[(product.category_id, base.upper())].append((size, product))

        groups = {}
        for key, rows in buckets.items():
            if len(rows) < 2:
                continue
            sizes = [s for s, _ in rows]
            if len(set(sizes)) != len(sizes):
                self.stderr.write(
                    f"  пропуск «{rows[0][1].name}»: два блюда с одинаковым объёмом"
                )
                continue
            groups[key] = sorted(rows, key=lambda r: r[0])
        return groups

    def _report(self, groups, unit):
        self.stdout.write(f"К объединению групп: {len(groups)}\n")
        for (_, base), rows in sorted(groups.items(), key=lambda kv: kv[0][1]):
            keep = rows[0][1]
            self.stdout.write(f"  «{split_name(keep.name)[0]}» ← {len(rows)} блюд")
            for size, product in rows:
                mark = "оставляем" if product.id == keep.id else "убираем"
                price = product.variants.all()[0].price
                self.stdout.write(
                    f"     {product.name:46} → вариант «{label_for(size, unit)}» "
                    f"{price:g} ₽   ({mark})"
                )

    # ── слияние ──────────────────────────────────────────────────────────
    def _merge(self, groups, unit):
        merged = dropped = 0
        for (_, _base), rows in groups.items():
            # Оставляем блюдо с наименьшим объёмом: его название без хвоста
            # и становится общим, а карточка обычно и есть «основная».
            keep = rows[0][1]
            keep.name = split_name(keep.name)[0]

            for order, (size, product) in enumerate(rows):
                variant = product.variants.all()[0]
                variant.product = keep
                variant.label = label_for(size, unit)
                variant.sort_order = order
                variant.save(update_fields=["product", "label", "sort_order"])

                if product.id == keep.id:
                    continue
                # Фото и описание подтягиваем с того, у кого они есть:
                # владелец мог заполнить карточку не у самого мелкого объёма.
                if not keep.image and product.image:
                    keep.image, keep.thumbnail = product.image, product.thumbnail
                if not keep.description and product.description:
                    keep.description = product.description
                self._move_likes(product, keep)
                product.delete()
                dropped += 1

            keep.save()
            merged += 1
        return merged, dropped

    @staticmethod
    def _move_likes(source: Product, target: Product):
        """Лайки переезжают на общую карточку; повторные с устройства гасим."""
        seen = set(
            ProductLike.objects.filter(product=target).values_list("device", flat=True)
        )
        for like in ProductLike.objects.filter(product=source):
            if like.device in seen:
                like.delete()
                continue
            like.product = target
            like.save(update_fields=["product"])
            seen.add(like.device)
