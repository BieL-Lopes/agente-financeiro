"""Modelos de dominio. Dataclasses puras: nenhum SQL, HTTP ou id de banco.

As transacoes referenciam conta e categoria por NOME, nao por id. Quem persiste
resolve os nomes para ids. Isso mantem o gerador de dados testavel sem banco.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class AccountKind(StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"


class CategoryKind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"


@dataclass(frozen=True)
class Account:
    name: str
    kind: AccountKind
    institution: str = ""


@dataclass(frozen=True)
class Category:
    name: str
    kind: CategoryKind


@dataclass(frozen=True)
class Transaction:
    account_name: str
    occurred_on: date
    amount: Decimal
    description: str
    category_name: str | None = None
