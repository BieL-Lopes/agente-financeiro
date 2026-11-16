import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from financeiro.domain.models import (
    Account,
    AccountKind,
    Category,
    CategoryKind,
    Transaction,
)


def test_account_kind_tem_os_tres_valores_do_schema():
    assert {k.value for k in AccountKind} == {"checking", "savings", "credit_card"}


def test_category_kind_tem_os_tres_valores_do_schema():
    assert {k.value for k in CategoryKind} == {"expense", "income", "transfer"}


def test_transaction_e_imutavel():
    t = Transaction(
        account_name="Conta Corrente Nubank",
        occurred_on=date(2026, 5, 2),
        amount=Decimal("-99.90"),
        description="VIVO FIBRA",
        category_name="servicos",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.amount = Decimal("0")  # type: ignore[misc]


def test_transaction_aceita_categoria_nula():
    t = Transaction(
        account_name="Conta Corrente Nubank",
        occurred_on=date(2026, 5, 2),
        amount=Decimal("-10.00"),
        description="PIX ENVIADO JOAO M S",
    )
    assert t.category_name is None


def test_account_e_category_sao_imutaveis():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Account(name="X", kind=AccountKind.CHECKING).name = "Y"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        Category(name="x", kind=CategoryKind.EXPENSE).name = "y"  # type: ignore[misc]
