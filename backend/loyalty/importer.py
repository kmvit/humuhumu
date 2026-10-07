"""Перенос гостей бонусной программы из файла прежней системы.

Каждое кафе приходит со своей выгрузкой: у Монти это Excel из a1-systems,
у следующего будет CSV из iiko или таблица, которую вели руками. Поэтому
колонки ищем по названиям, а не по номерам, и берём только то, что
нужно программе: телефон, имя, день рождения и остаток бонусов.

Работает в два шага. Сначала разбор — ничего не пишет и показывает, кто
перенесётся, кого пропустим и почему. Потом перенос тех же строк. Гость,
чей телефон у нас уже есть, пропускается: его баланс живой, а файл из
старой системы о нём не знает. Поэтому повторная загрузка того же файла
ничего не удваивает — все окажутся «уже в программе».
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from django.db import transaction
from rest_framework import serializers

from .models import LoyaltyMember
from .serializers import normalize_phone

#: Больше строк в выгрузке маленького кафе не бывает; защита от того,
#: чтобы по ошибке не загрузили, скажем, журнал чеков на миллион строк.
MAX_ROWS = 20000

#: Как колонка может называться в разных системах. Сравниваем по
#: нормализованному заголовку (см. _norm). Порядок важен: первым идёт
#: точное название — «текущ баланс» раньше просто «бонус», потому что у
#: a1-systems в колонке «Бонус» лежит процент начисления, а не остаток.
COLUMNS = {
    "phone": ["телефон", "мобильный телефон", "моб телефон", "номер телефона", "phone", "mobile"],
    "name": ["имя", "фио", "имя клиента", "клиент", "гость", "name"],
    "birth_date": ["день рождения", "дата рождения", "др", "birthday", "birth date"],
    "balance": [
        "текущ баланс", "текущий баланс", "баланс бонусов", "бонусный баланс",
        "остаток бонусов", "баланс", "бонусы", "balance",
    ],
}


def _norm(header) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(header or "").lower().replace("ё", "е")).split())


class ImportFileError(Exception):
    """Файл не разобрать: не тот формат или нет колонки с телефоном."""


def read_rows(upload) -> list[list]:
    """Таблица из файла: .xlsx — первый лист, .csv — любая кодировка из двух."""
    name = (getattr(upload, "name", "") or "").lower()
    raw = upload.read()
    if name.endswith(".xlsx") or raw[:2] == b"PK":
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception as e:  # битый или не Excel
            raise ImportFileError("Не получилось открыть файл как Excel (.xlsx)") from e
        rows = []
        for row in wb.worksheets[0].iter_rows(values_only=True):
            rows.append(list(row))
            if len(rows) > MAX_ROWS + 1:
                raise ImportFileError(f"В файле больше {MAX_ROWS} строк — это точно список гостей?")
        return rows
    if name.endswith(".xls"):
        raise ImportFileError("Старый формат .xls не читаем — сохраните файл как .xlsx или .csv")
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportFileError("Не получилось прочитать файл: сохраните его как .xlsx или .csv")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if len(rows) > MAX_ROWS + 1:
        raise ImportFileError(f"В файле больше {MAX_ROWS} строк — это точно список гостей?")
    return rows


def find_columns(header: list) -> dict[str, int]:
    """{поле: номер колонки}. Телефон обязателен — без него гостя не найти."""
    normed = [_norm(h) for h in header]
    found: dict[str, int] = {}
    for key, names in COLUMNS.items():
        for wanted in names:
            if wanted in normed:
                found[key] = normed.index(wanted)
                break
    if "phone" not in found:
        raise ImportFileError(
            "Не нашли колонку с телефоном. Первой строкой файла должны идти "
            "названия колонок, одна из них — «Телефон»."
        )
    return found


def _cell(row: list, idx: int | None):
    if idx is None or idx >= len(row):
        return None
    value = row[idx]
    if isinstance(value, str):
        value = value.strip()
    return value if value not in ("", None) else None


def _birth(value) -> date | None:
    """Дата рождения из того, как её пишут: 20.10.1995, 1995-10-20, ячейка Excel."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        parsed = value
    else:
        parsed = None
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"):
            try:
                parsed = datetime.strptime(str(value).split()[0], fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError
    # «01.01.1900» и будущие даты — заглушки, а не дни рождения
    if parsed.year < 1920 or parsed > date.today():
        raise ValueError
    return parsed


def _balance(value) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        amount = Decimal(str(value).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except InvalidOperation as e:
        raise ValueError from e
    if amount < 0 or not amount.is_finite():
        raise ValueError
    return amount


@dataclass
class Plan:
    """Что сделает перенос. Строки — номер в файле (как видит человек в Excel)."""

    columns: dict[str, str]
    total: int = 0
    to_import: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    invalid: list[dict] = field(default_factory=list)
    #: предупреждения по строкам, которые всё же перенесутся
    notes: list[dict] = field(default_factory=list)

    @property
    def balance_total(self) -> Decimal:
        return sum((g["balance"] for g in self.to_import), Decimal("0"))

    def as_dict(self) -> dict:
        return {
            "columns": self.columns,
            "total": self.total,
            "to_import": [
                {**g, "birth_date": g["birth_date"].isoformat() if g["birth_date"] else None,
                 "balance": str(g["balance"]), "balance_in_file": str(g["balance_in_file"])}
                for g in self.to_import
            ],
            "skipped": self.skipped,
            "invalid": self.invalid,
            "notes": self.notes,
            "balance_total": str(self.balance_total),
        }


def plan_import(upload) -> Plan:
    """Разобрать файл и решить по каждой строке. Ничего не пишет."""
    rows = read_rows(upload)
    if not rows:
        raise ImportFileError("Файл пустой")
    header, body = rows[0], rows[1:]
    cols = find_columns(header)
    plan = Plan(columns={key: str(header[idx]) for key, idx in cols.items()})

    existing = {
        m.user.phone: m
        for m in LoyaltyMember.objects.select_related("user").exclude(user__phone=None)
    }
    seen: dict[str, int] = {}
    for n, row in enumerate(body, start=2):
        if not any(_cell(row, i) is not None for i in range(len(row))):
            continue  # пустая строка в конце таблицы
        plan.total += 1
        name = str(_cell(row, cols.get("name")) or "").strip()[:150]
        raw_phone = _cell(row, cols["phone"])
        try:
            phone = normalize_phone(str(raw_phone or ""))
        except serializers.ValidationError:
            plan.invalid.append({"row": n, "name": name, "reason": f"телефон «{raw_phone or ''}» не похож на номер"})
            continue
        try:
            in_file = _balance(_cell(row, cols.get("balance")))
        except ValueError:
            plan.invalid.append({"row": n, "name": name, "reason": "остаток бонусов не число"})
            continue

        if phone in seen:
            plan.skipped.append({"row": n, "name": name, "phone": phone, "reason": f"повтор строки {seen[phone]}"})
            continue
        seen[phone] = n
        if phone in existing:
            m = existing[phone]
            plan.skipped.append({
                "row": n, "name": name, "phone": phone,
                "reason": f"уже в программе ({m.name}, {m.balance.normalize():f} б.)",
            })
            continue

        try:
            birth = _birth(_cell(row, cols.get("birth_date")))
        except ValueError:
            # день рождения — не повод терять гостя с бонусами
            birth = None
            plan.notes.append({"row": n, "name": name, "note": "дата рождения не распознана — перенесём без неё"})

        plan.to_import.append({
            "row": n, "name": name, "phone": phone, "birth_date": birth,
            # бонусы целые — как и везде в программе, дробь отбрасываем
            "balance": in_file.quantize(Decimal("1"), rounding=ROUND_DOWN),
            "balance_in_file": in_file,
        })
    return plan


@transaction.atomic
def run_import(plan: Plan, *, consent: bool) -> list[LoyaltyMember]:
    """Перенести гостей из плана. Всё или ничего: половина базы хуже, чем ноль —
    непонятно, кого уже перенесли, а повтор пропустит перенесённых молча."""
    from .services import transfer_member

    return [
        transfer_member(g["phone"], g["name"], g["birth_date"], g["balance"], consent=consent)
        for g in plan.to_import
    ]
