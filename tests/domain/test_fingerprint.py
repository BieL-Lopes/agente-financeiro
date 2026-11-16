from datetime import date
from decimal import Decimal

from financeiro.domain.fingerprint import transaction_fingerprint

CONTA = "Conta Corrente Nubank"
DIA = date(2026, 3, 14)


def test_mesma_entrada_mesmo_hash():
    a = transaction_fingerprint(CONTA, DIA, Decimal("-120.50"), "SUPERM BOM PRECO LT")
    b = transaction_fingerprint(CONTA, DIA, Decimal("-120.50"), "SUPERM BOM PRECO LT")
    assert a == b
    assert len(a) == 64


def test_normaliza_caixa_e_espaco_da_descricao():
    a = transaction_fingerprint(CONTA, DIA, Decimal("-10.00"), "UBER  *TRIP")
    b = transaction_fingerprint(CONTA, DIA, Decimal("-10.00"), "  uber *trip  ")
    assert a == b


def test_decimal_equivalente_gera_mesmo_hash():
    a = transaction_fingerprint(CONTA, DIA, Decimal("-10.5"), "NETFLIX.COM")
    b = transaction_fingerprint(CONTA, DIA, Decimal("-10.50"), "NETFLIX.COM")
    assert a == b


def test_valor_diferente_gera_hash_diferente():
    a = transaction_fingerprint(CONTA, DIA, Decimal("-10.00"), "NETFLIX.COM")
    b = transaction_fingerprint(CONTA, DIA, Decimal("-10.01"), "NETFLIX.COM")
    assert a != b


def test_conta_diferente_gera_hash_diferente():
    a = transaction_fingerprint("Conta A", DIA, Decimal("-10.00"), "X")
    b = transaction_fingerprint("Conta B", DIA, Decimal("-10.00"), "X")
    assert a != b
