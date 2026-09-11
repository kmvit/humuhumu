"""Бизнес-логика бонусов. 1 бонус = 1 ₽.

Все операции атомарны и блокируют участника: официант на кассе и гость
в приложении могут списывать одновременно, и без блокировки баланс
разъехался бы с журналом.
"""
from decimal import ROUND_DOWN, Decimal

from django.db import transaction

from core.models import SiteSettings

from .models import BonusTransaction, LoyaltyMember


class LoyaltyError(Exception):
    pass


def _whole(value: Decimal) -> Decimal:
    """Бонусы — целые: пол-бонуса гость не потратит и в чеке не покажешь."""
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_DOWN)


def _apply(member: LoyaltyMember, *, type_: str, amount: Decimal, order=None, comment=""):
    """Внутренняя проводка. Только внутри atomic с заблокированным участником."""
    member.balance += amount
    member.save(update_fields=["balance"])
    return BonusTransaction.objects.create(
        member=member,
        type=type_,
        amount=amount,
        balance_after=member.balance,
        order=order,
        comment=comment,
    )


@transaction.atomic
def enroll(user, birth_date=None) -> LoyaltyMember:
    """Записать гостя в программу и выдать приветственные бонусы.

    Повторный вызов приветственные не удваивает: они привязаны к участнику,
    а не к вызову — гость может «зарегистрироваться» ещё раз с того же номера.
    """
    site = SiteSettings.load()
    if not site.bonus_enabled:
        raise LoyaltyError("Бонусная программа выключена")
    member, created = LoyaltyMember.objects.get_or_create(
        user=user, defaults={"birth_date": birth_date}
    )
    if not created and birth_date and not member.birth_date:
        member.birth_date = birth_date
        member.save(update_fields=["birth_date"])
    if created and site.bonus_welcome:
        member = LoyaltyMember.objects.select_for_update().get(pk=member.pk)
        _apply(
            member,
            type_=BonusTransaction.Type.WELCOME,
            amount=Decimal(site.bonus_welcome),
            comment="Приветственные бонусы",
        )
    return member


@transaction.atomic
def enroll_by_phone(phone: str, name: str = "", birth_date=None) -> LoyaltyMember:
    """Записать в программу по телефону: найти гостя или завести нового.

    Телефон — ключ программы: гость мог заказывать раньше и уже быть в базе,
    тогда второго пользователя не плодим. Нужен и на кассе, и в форме заказа,
    поэтому живёт здесь, а не в сериализаторе регистрации.
    """
    from users.models import User

    user = User.tenant.filter(phone=phone).first()
    if user is None:
        user = User(
            username=phone,
            phone=phone,
            first_name=(name or "").strip(),
            role=User.Role.CLIENT,
        )
        user.set_unusable_password()  # пароль выдаём отдельно, при рассылке
        user.save()
    elif not user.first_name and (name or "").strip():
        user.first_name = name.strip()
        user.save(update_fields=["first_name"])
    return enroll(user, birth_date)


@transaction.atomic
def redeem(member_id: int, amount: Decimal, order) -> BonusTransaction:
    """Списать бонусы в счёт заказа.

    Списать больше, чем стоит заказ, нельзя: остаток бонусов не превращается
    в сдачу. Уже списанное по этому заказу учитывается — иначе повторный
    вызов увёл бы сумму к оплате в минус.
    """
    site = SiteSettings.load()
    if not site.bonus_enabled:
        raise LoyaltyError("Бонусная программа выключена")
    amount = _whole(amount)
    if amount <= 0:
        raise LoyaltyError("Сумма списания должна быть положительной")
    member = LoyaltyMember.objects.select_for_update().get(pk=member_id)
    if member.balance < amount:
        raise LoyaltyError(f"На счету только {_whole(member.balance)} бонусов")
    room = Decimal(order.total) - Decimal(order.bonus_spent)
    if amount > room:
        raise LoyaltyError(f"К списанию доступно не больше {_whole(room)} бонусов")
    txn = _apply(
        member,
        type_=BonusTransaction.Type.REDEEM,
        amount=-amount,
        order=order,
        comment=f"Оплата заказа №{order.pk}",
    )
    order.bonus_spent = Decimal(order.bonus_spent) + amount
    order.save(update_fields=["bonus_spent"])
    return txn


@transaction.atomic
def earn_for_order(order) -> BonusTransaction | None:
    """Начислить бонусы за оплаченный заказ.

    Процент считаем от суммы, оплаченной деньгами: начислять бонусы на часть,
    закрытую бонусами же, — самоподпитка, при которой баланс не тратится.
    Повторный вызов ничего не делает: у заказа одно начисление.
    """
    site = SiteSettings.load()
    member = getattr(order.client, "loyalty", None) if order.client else None
    if not site.bonus_enabled or member is None:
        return None
    if BonusTransaction.objects.filter(
        order=order, type=BonusTransaction.Type.EARN
    ).exists():
        return None
    paid = Decimal(order.total) - Decimal(order.bonus_spent)
    amount = _whole(paid * Decimal(site.bonus_earn_percent) / Decimal(100))
    if amount <= 0:
        return None
    member = LoyaltyMember.objects.select_for_update().get(pk=member.pk)
    return _apply(
        member,
        type_=BonusTransaction.Type.EARN,
        amount=amount,
        order=order,
        comment=f"Заказ №{order.pk}",
    )


@transaction.atomic
def return_for_order(order) -> BonusTransaction | None:
    """Вернуть списанные бонусы, если заказ отменили.

    Без возврата гость теряет бонусы за отменённый заказ — деньги ему
    возвращают, а бонусы нет.
    """
    member = getattr(order.client, "loyalty", None) if order.client else None
    spent = Decimal(order.bonus_spent or 0)
    if member is None or spent <= 0:
        return None
    member = LoyaltyMember.objects.select_for_update().get(pk=member.pk)
    txn = _apply(
        member,
        type_=BonusTransaction.Type.RETURN,
        amount=spent,
        order=order,
        comment=f"Возврат по отменённому заказу №{order.pk}",
    )
    order.bonus_spent = Decimal(0)
    order.save(update_fields=["bonus_spent"])
    return txn
