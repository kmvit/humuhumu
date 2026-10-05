"""Данные для чека онлайн-оплаты (54-ФЗ): позиции и контакт гостя.

Чек пробивает не наша система, а онлайн-касса, подключённая к банку
(у Монти — Бизнес.Ру через ЮKassa). Банк с такой кассой не примет платёж
без блока чека: позиций, ставки НДС и телефона или почты, куда отправить
чек. Здесь — то, что от банка не зависит; как это упаковать, решает
драйвер (acquiring.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

KOPECK = Decimal("0.01")

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class Line:
    """Строка чека: цена за штуку уже со скидкой бонусами."""

    name: str
    quantity: int
    price: Decimal

    @property
    def total(self) -> Decimal:
        return self.price * self.quantity


def lines(order, amount: Decimal) -> list[Line]:
    """Позиции заказа, которые в сумме дают ровно amount.

    Банк сверяет сумму чека с суммой платежа до копейки и при расхождении
    платёж отклоняет. Бонусы гостя — скидка, а отдельной строки «скидка» в
    чеке нет: её раскладываем по позициям пропорционально их стоимости.
    Если цена за штуку после скидки не делится на копейки, строка
    разбивается на две (N−1 шт. по одной цене и 1 шт. по другой) — иначе
    цена × количество разойдётся с суммой.
    """
    amount = Decimal(amount).quantize(KOPECK)
    gross: list[tuple[str, int, Decimal]] = []
    for item in order.items.all():
        name = item.display_name
        if item.options_text:
            name = f"{name} ({item.options_text})"
        unit = Decimal((item.unit_price or 0) + item.modifiers_total).quantize(KOPECK)
        qty = int(item.quantity or 0)
        # Бесплатная позиция в чек не идёт: банк не принимает нулевые суммы.
        if qty > 0 and unit > 0:
            gross.append((name[:128], qty, unit))

    total = sum((unit * qty for _, qty, unit in gross), start=Decimal("0"))
    if not gross or amount <= 0 or amount > total:
        raise ValueError(f"Сумма чека {total} не сходится с оплатой {amount}")

    discount = total - amount
    # Скидка на строку — пропорционально, с округлением вниз; остаток копеек
    # уходит на самую дорогую строку, ей он точно по силам.
    shares = [
        (discount * unit * qty / total).quantize(KOPECK, rounding=ROUND_DOWN)
        for _, qty, unit in gross
    ]
    biggest = max(range(len(gross)), key=lambda i: gross[i][2] * gross[i][1])
    shares[biggest] += discount - sum(shares, start=Decimal("0"))

    result: list[Line] = []
    for (name, qty, unit), share in zip(gross, shares):
        line_sum = unit * qty - share
        price = (line_sum / qty).quantize(KOPECK, rounding=ROUND_DOWN)
        rest = line_sum - price * qty
        if rest == 0:
            result.append(Line(name, qty, price))
        else:
            if qty > 1:
                result.append(Line(name, qty - 1, price))
            result.append(Line(name, 1, line_sum - price * (qty - 1)))
    return result


def parse_contact(raw: str) -> str:
    """Телефон («79991234567») или почта из того, что ввёл гость.

    Пусто — если на телефон или почту не похоже. Телефон приводим к виду,
    который принимают и банки, и кассы: 11 цифр, начиная с 7.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if "@" in value:
        return value.lower() if _EMAIL.fullmatch(value) else ""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10:
        return "7" + digits
    return ""


def is_email(contact: str) -> bool:
    return "@" in contact


def contact_of(order) -> str:
    """Контакт, который у заказа уже есть: введённый раньше или из профиля.

    Гость бонусной программы оставил телефон при входе — спрашивать его
    второй раз незачем.
    """
    if order.receipt_contact:
        return order.receipt_contact
    client = getattr(order, "client", None)
    if client is not None:
        for raw in (getattr(client, "phone", ""), getattr(client, "email", "")):
            contact = parse_contact(raw or "")
            if contact:
                return contact
    return ""
