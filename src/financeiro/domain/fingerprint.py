"""Hash estavel de uma transacao, usado para deduplicar importacoes repetidas."""

import hashlib
from datetime import date
from decimal import Decimal


def transaction_fingerprint(
    account_name: str,
    occurred_on: date,
    amount: Decimal,
    description: str,
) -> str:
    """Devolve o sha256 hex de uma transacao.

    Normaliza a descricao (caixa baixa, espacos colapsados) e o valor (duas
    casas) para que o mesmo lancamento importado de duas fontes produza o
    mesmo hash.
    """
    normalized_description = " ".join(description.split()).lower()
    raw = "|".join(
        [
            account_name.strip().lower(),
            occurred_on.isoformat(),
            f"{amount:.2f}",
            normalized_description,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
