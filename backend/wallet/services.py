"""Бизнес-логика токенов. Все операции атомарны и блокируют кошелёк."""
from decimal import Decimal

from django.db import transaction

from .models import TokenPackage, TokenTransaction, Wallet


class InsufficientFunds(Exception):
    """Недостаточно токенов на кошельке."""


def _apply(wallet: Wallet, *, type_: str, amount: Decimal, order=None, comment="") -> TokenTransaction:
    """Внутренняя проводка. Вызывать только внутри transaction.atomic с заблокированным кошельком."""
    wallet.balance += amount
    wallet.save(update_fields=["balance"])
    return TokenTransaction.objects.create(
        wallet=wallet,
        type=type_,
        amount=amount,
        balance_after=wallet.balance,
        order=order,
        comment=comment,
    )


@transaction.atomic
def spend_tokens(wallet_id: int, amount: Decimal, order=None) -> TokenTransaction:
    """Списать токены за заказ. Бросает InsufficientFunds, если не хватает."""
    wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
    if wallet.balance < amount:
        raise InsufficientFunds()
    return _apply(wallet, type_=TokenTransaction.Type.PURCHASE, amount=-amount, order=order)


@transaction.atomic
def topup_by_package(wallet_id: int, package: TokenPackage) -> list[TokenTransaction]:
    """Пополнить кошелёк по пакету: основная сумма + бонус. Вызывать ТОЛЬКО из webhook'а оплаты."""
    wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
    txns = [_apply(wallet, type_=TokenTransaction.Type.TOPUP, amount=package.pay_amount)]
    if package.bonus_amount:
        txns.append(
            _apply(wallet, type_=TokenTransaction.Type.BONUS, amount=package.bonus_amount)
        )
    return txns
