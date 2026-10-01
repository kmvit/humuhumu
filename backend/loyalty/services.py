"""Бизнес-логика бонусов. 1 бонус = 1 ₽.

Все операции атомарны и блокируют участника: официант на кассе и гость
в приложении могут списывать одновременно, и без блокировки баланс
разъехался бы с журналом.
"""
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

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
def enroll(user, birth_date=None, *, source=None, consent=False) -> LoyaltyMember:
    """Записать гостя в программу и выдать приветственные бонусы.

    Повторный вызов приветственные не удваивает: они привязаны к участнику,
    а не к вызову — гость может «зарегистрироваться» ещё раз с того же номера.
    """
    site = SiteSettings.load()
    if not site.bonus_enabled:
        raise LoyaltyError("Бонусная программа выключена")
    now = timezone.now() if consent else None
    member, created = LoyaltyMember.objects.get_or_create(
        user=user,
        defaults={
            "birth_date": birth_date,
            "source": source or LoyaltyMember.Source.GUEST,
            "consent_at": now,
        },
    )
    if not created:
        changed = []
        if birth_date and not member.birth_date:
            member.birth_date = birth_date
            changed.append("birth_date")
        if now and not member.consent_at:
            member.consent_at = now
            changed.append("consent_at")
        if changed:
            member.save(update_fields=changed)
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
def enroll_by_phone(
    phone: str, name: str = "", birth_date=None, *, source=None, consent=False
) -> LoyaltyMember:
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
    return enroll(user, birth_date, source=source, consent=consent)


@transaction.atomic
def transfer_member(
    phone: str, name: str, birth_date=None, balance=0, *, consent=False
) -> LoyaltyMember:
    """Перенести гостя из прежней системы: с его остатком, без приветственных.

    Приветственные он уже получал там — второй раз было бы подарком за
    переезд. Остаток ложится отдельной проводкой, чтобы в журнале было
    видно, откуда у гостя бонусы. Гость с этим телефоном уже есть — не
    трогаем: его баланс у нас живой, и файл из старой системы его не знает.
    """
    from users.models import User

    if not SiteSettings.load().bonus_enabled:
        raise LoyaltyError("Бонусная программа выключена")
    if LoyaltyMember.objects.filter(user__phone=phone).exists():
        raise LoyaltyError("Гость с этим телефоном уже в программе")
    balance = _whole(Decimal(balance or 0))
    if balance < 0:
        raise LoyaltyError("Остаток не может быть отрицательным")
    user = User.tenant.filter(phone=phone).first()
    if user is None:
        user = User(
            username=phone, phone=phone,
            first_name=(name or "").strip(), role=User.Role.CLIENT,
        )
        user.set_unusable_password()
        user.save()
    member = LoyaltyMember.objects.create(
        user=user,
        birth_date=birth_date,
        source=LoyaltyMember.Source.IMPORT,
        consent_at=timezone.now() if consent else None,
    )
    if balance > 0:
        member = LoyaltyMember.objects.select_for_update().get(pk=member.pk)
        _apply(
            member,
            type_=BonusTransaction.Type.IMPORT,
            amount=balance,
            comment="Остаток из прежней системы",
        )
    return member


@transaction.atomic
def attach_guest(order, member: LoyaltyMember) -> BonusTransaction | None:
    """Закрепить заказ за гостем: гость назвал телефон, бонусы копит.

    Без этого заказ к гостю привязывало только списание, и тот, кто
    бонусы копит, а не тратит, за чек ничего не получал. Если деньги уже
    взяли (на стойке платят вперёд), начисляем сразу: оплата прошла без
    гостя, и второго случая начислить не будет.

    Сменить гостя можно, пока по заказу не было бонусных операций: иначе
    списанное или начисленное одному осталось бы висеть на другом.
    """
    from orders.models import Order

    if not SiteSettings.load().bonus_enabled:
        raise LoyaltyError("Бонусная программа выключена")
    order = Order.objects.select_for_update().get(pk=order.pk)
    # Только до выдачи: иначе сотрудник мог бы вписывать свой номер в
    # чужие закрытые чеки и собирать за них бонусы.
    if order.status not in (Order.Status.OPEN, Order.Status.UNPAID):
        raise LoyaltyError("Гостя можно указать только до закрытия заказа")
    if order.client_id == member.user_id:
        return None
    if order.client_id and BonusTransaction.objects.filter(order=order).exists():
        raise LoyaltyError("По заказу уже были бонусы другого гостя")
    order.client = member.user
    order.save(update_fields=["client"])
    if order.paid_at:
        return earn_for_order(order)
    return None


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
def return_for_order(order, *, refund: bool = False) -> BonusTransaction | None:
    """Вернуть списанные бонусы, если заказ отменили или по нему вернули деньги.

    Без возврата гость теряет бонусы за отменённый заказ — деньги ему
    возвращают, а бонусы нет.

    При отмене списание с заказа снимаем: заказа не было, и повторная
    отмена не вернёт бонусы дважды. При возврате денег (refund=True) —
    оставляем: заказ был продан с оплатой частью бонусами, на этом стоит
    выручка дня продажи и сумма платежа возврата. Дважды не вернёт статус
    «Возврат» — второй раз refund_order не пропустит.
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
        comment=(
            f"Возврат денег по заказу №{order.pk}"
            if refund
            else f"Возврат по отменённому заказу №{order.pk}"
        ),
    )
    if not refund:
        order.bonus_spent = Decimal(0)
        order.save(update_fields=["bonus_spent"])
    return txn


@transaction.atomic
def cancel_earned_for_order(order) -> BonusTransaction | None:
    """Снять бонусы, начисленные за заказ, которому сделали возврат.

    Без этого гость получает бонусы за покупку, которой не было: деньги
    ему вернули, а баллы остались — и он спишет их со следующего заказа.
    Ниже нуля баланс не уводим: гость мог успеть их потратить, и уходить
    в минус из-за нашей же задержки нечестно.
    """
    member = getattr(order.client, "loyalty", None) if order.client else None
    if member is None:
        return None

    earned = (
        BonusTransaction.objects.filter(order=order, type=BonusTransaction.Type.EARN)
        .aggregate(s=Sum("amount"))["s"]
    )
    earned = Decimal(earned or 0)
    if earned <= 0:
        return None

    member = LoyaltyMember.objects.select_for_update().get(pk=member.pk)
    amount = min(earned, member.balance)
    if amount <= 0:
        return None
    return _apply(
        member,
        type_=BonusTransaction.Type.ADJUST,
        amount=-amount,
        order=order,
        comment=f"Снятие начисленных: возврат по заказу №{order.pk}",
    )
