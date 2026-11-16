# Etapa 1 — Estrutura, banco e seed sintético — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar um PostgreSQL em Docker com schema de três tabelas, dois papéis de banco com permissões distintas, runner de migrations em SQL puro, e um gerador de 12 meses de transações sintéticas determinístico com testes de propriedade.

**Architecture:** O gerador de dados é uma função **pura** (`build_dataset`) que devolve dataclasses, sem tocar no banco — assim as propriedades interessantes (determinismo, tendência plantada, fatura que casa com o cartão) são testadas como unidade, em milissegundos, sem PostgreSQL. Um `writer` fino e separado persiste o resultado. As migrations são arquivos `.sql` numerados aplicados por um runner que registra versões em `schema_migrations`.

**Tech Stack:** Python 3.12, psycopg 3, Pytest, PostgreSQL 17, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-16-agente-financeiro-design.md`

## Global Constraints

Valem para toda task; cada task herda esta seção implicitamente.

- **Python 3.12.** `requires-python = ">=3.12"`.
- **Nenhum dado financeiro real** entra no repositório, em nenhuma etapa, nem em fixture de teste.
- **`SEED_REFERENCE_DATE = date(2026, 12, 31)`**, constante. **Nunca `date.today()`** em código de seed ou de teste.
- **`random.Random(42)` explícito.** Nunca o módulo `random` global.
- **Dinheiro é `Decimal`** em Python e `numeric(12,2)` no banco. **Nunca `float`.**
- **Gasto é `categories.kind = 'expense'`**, nunca "amount negativo".
- **Toda migration vai para o git**, sempre.
- **Nome de migration com 3 dígitos zero-padded** (`001_`, `002_`, …): o runner ordena por nome.
- **Porta 5433 no host** para o PostgreSQL.
- **A etapa 1 funciona sem `ANTHROPIC_API_KEY`.** Nenhuma task abaixo importa `anthropic`.
- As senhas em `.env.example` e nos defaults são de desenvolvimento local e **não são segredo**; o `.env` real fica no `.gitignore`.

## Estrutura de arquivos

| arquivo | responsabilidade |
|---|---|
| `pyproject.toml` | dependências, config do pytest |
| `.gitignore`, `.env.example` | ignorados e documentação de variáveis |
| `Dockerfile` | imagem do serviço `api` (usada para rodar comandos no compose) |
| `docker-compose.yml` | serviços `db` e `api` |
| `docker/initdb/00-roles.sql` | cria `financeiro_app` e `financeiro_etl` |
| `docker/initdb/01-test-database.sql` | cria `financeiro_test` |
| `src/financeiro/config.py` | `Settings` lido do ambiente, com defaults locais |
| `src/financeiro/domain/models.py` | dataclasses e enums; zero I/O |
| `src/financeiro/domain/fingerprint.py` | hash de deduplicação de transação |
| `src/financeiro/db/connection.py` | fábricas de conexão por papel |
| `src/financeiro/db/migrate.py` | runner de migrations + CLI |
| `src/financeiro/db/migrations/001_create_finance_tables.sql` | schema e grants |
| `src/financeiro/seed/reference.py` | contas, categorias, lista de meses |
| `src/financeiro/seed/recurring.py` | transações de valor e dia fixos |
| `src/financeiro/seed/variable.py` | transações variáveis, anomalia, férias |
| `src/financeiro/seed/transfers.py` | fatura do cartão e aporte na poupança |
| `src/financeiro/seed/generator.py` | orquestra tudo; `build_dataset` puro |
| `src/financeiro/seed/writer.py` | persiste o `Dataset` via papel `etl` |
| `src/financeiro/seed/__main__.py` | CLI `python -m financeiro.seed` |
| `tests/conftest.py` | fixtures de banco de teste |

---

### Task 1: Esqueleto do projeto e domínio puro

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/financeiro/__init__.py`, `src/financeiro/domain/__init__.py`, `src/financeiro/domain/models.py`, `src/financeiro/domain/fingerprint.py`
- Test: `tests/__init__.py`, `tests/domain/__init__.py`, `tests/domain/test_fingerprint.py`, `tests/domain/test_models.py`

**Interfaces:**
- Consumes: nada (primeira task).
- Produces: `AccountKind`, `CategoryKind` (`StrEnum`); `Account(name: str, kind: AccountKind, institution: str = "")`; `Category(name: str, kind: CategoryKind)`; `Transaction(account_name: str, occurred_on: date, amount: Decimal, description: str, category_name: str | None = None)` — todas `@dataclass(frozen=True)`. `transaction_fingerprint(account_name: str, occurred_on: date, amount: Decimal, description: str) -> str` devolve hex sha256 de 64 caracteres.

- [ ] **Step 1: Criar `pyproject.toml`**

```toml
[project]
name = "financeiro"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["psycopg[binary]>=3.2"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["db: precisa de PostgreSQL rodando"]
addopts = "-ra"
```

- [ ] **Step 2: Criar `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
venv/
*.egg-info/
build/
dist/
.env
.coverage
htmlcov/
```

- [ ] **Step 3: Criar os `__init__.py` vazios**

```bash
mkdir -p src/financeiro/domain tests/domain
touch src/financeiro/__init__.py src/financeiro/domain/__init__.py
touch tests/__init__.py tests/domain/__init__.py
```

- [ ] **Step 3b: Criar o venv e instalar o pacote em modo editável**

Sem isto, `import financeiro` falha e todos os testes deste plano erram com
`ModuleNotFoundError`.

```bash
python -m venv .venv
# Windows (Git Bash):
source .venv/Scripts/activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

Verificar: `python -c "import financeiro; print(financeiro.__file__)"` imprime um
caminho dentro de `src/financeiro/`.

- [ ] **Step 4: Escrever o teste do fingerprint, que falha**

Criar `tests/domain/test_fingerprint.py`:

```python
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
```

- [ ] **Step 5: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/domain/test_fingerprint.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.domain.fingerprint'`

- [ ] **Step 6: Implementar o fingerprint**

Criar `src/financeiro/domain/fingerprint.py`:

```python
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
```

- [ ] **Step 7: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/domain/test_fingerprint.py -v`
Expected: PASS, 5 testes

- [ ] **Step 8: Escrever o teste dos modelos, que falha**

Criar `tests/domain/test_models.py`:

```python
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
```

- [ ] **Step 9: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/domain/test_models.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.domain.models'`

- [ ] **Step 10: Implementar os modelos**

Criar `src/financeiro/domain/models.py`:

```python
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
```

- [ ] **Step 11: Rodar a suíte completa**

Run: `python -m pytest -v`
Expected: PASS, 10 testes

- [ ] **Step 12: Commit**

```bash
git add pyproject.toml .gitignore src/financeiro tests
git commit -m "feat: esqueleto do projeto e modelos de dominio"
```

---

### Task 2: Docker Compose, papéis de banco e banco de teste

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.env.example`, `docker/initdb/00-roles.sql`, `docker/initdb/01-test-database.sql`

**Interfaces:**
- Consumes: nada.
- Produces: um PostgreSQL em `localhost:5433` com os bancos `financeiro` e `financeiro_test`, e os papéis `financeiro_app` (somente `SELECT`, `statement_timeout` de 5s) e `financeiro_etl` (leitura e escrita). Senhas iguais aos nomes dos papéis.

- [ ] **Step 1: Criar o script de papéis**

Criar `docker/initdb/00-roles.sql`:

```sql
-- Roda UMA VEZ, na criacao do volume do Postgres.
-- Alterar este arquivo exige `docker compose down -v` para ter efeito.
create role financeiro_app with login password 'financeiro_app';
create role financeiro_etl with login password 'financeiro_etl';

-- Timeout por query no papel do agente: a segunda camada contra query longa.
alter role financeiro_app set statement_timeout = '5s';
```

- [ ] **Step 2: Criar o script do banco de teste**

Criar `docker/initdb/01-test-database.sql`:

```sql
-- Banco separado para a suite de testes de integracao.
create database financeiro_test owner postgres;

grant connect on database financeiro to financeiro_app, financeiro_etl;
grant connect on database financeiro_test to financeiro_app, financeiro_etl;
```

- [ ] **Step 3: Criar o `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

COPY tests ./tests

CMD ["python", "-c", "print('use docker compose run --rm api <comando>')"]
```

- [ ] **Step 4: Criar o `docker-compose.yml`**

```yaml
services:
  db:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: financeiro
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      # 5433 no host para nao colidir com um Postgres instalado localmente.
      - "5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./docker/initdb:/docker-entrypoint-initdb.d:ro
    healthcheck:
      # depends_on sozinho espera o container subir, nao o Postgres aceitar
      # conexao. Sem healthcheck, a api sobe, falha em conectar, e morre.
      test: ["CMD-SHELL", "pg_isready -U postgres -d financeiro"]
      interval: 2s
      timeout: 3s
      retries: 20

  api:
    build: .
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL_OWNER: postgresql://postgres:postgres@db:5432/financeiro
      DATABASE_URL_APP: postgresql://financeiro_app:financeiro_app@db:5432/financeiro
      DATABASE_URL_ETL: postgresql://financeiro_etl:financeiro_etl@db:5432/financeiro
      DATABASE_URL_TEST_OWNER: postgresql://postgres:postgres@db:5432/financeiro_test
      DATABASE_URL_TEST_APP: postgresql://financeiro_app:financeiro_app@db:5432/financeiro_test
      DATABASE_URL_TEST_ETL: postgresql://financeiro_etl:financeiro_etl@db:5432/financeiro_test

volumes:
  pgdata:
```

- [ ] **Step 5: Criar o `.env.example`**

```dotenv
# Desenvolvimento local. Estas senhas nao sao segredo: o banco so escuta em
# localhost e os dados sao sinteticos. Copie para .env se quiser sobrescrever.

# Dono do banco: aplica migrations.
DATABASE_URL_OWNER=postgresql://postgres:postgres@localhost:5433/financeiro
# Papel do agente: somente SELECT.
DATABASE_URL_APP=postgresql://financeiro_app:financeiro_app@localhost:5433/financeiro
# Papel de carga: seed, importador, categorizacao.
DATABASE_URL_ETL=postgresql://financeiro_etl:financeiro_etl@localhost:5433/financeiro

# Banco da suite de testes.
DATABASE_URL_TEST_OWNER=postgresql://postgres:postgres@localhost:5433/financeiro_test
DATABASE_URL_TEST_APP=postgresql://financeiro_app:financeiro_app@localhost:5433/financeiro_test
DATABASE_URL_TEST_ETL=postgresql://financeiro_etl:financeiro_etl@localhost:5433/financeiro_test

# Nao usada na etapa 1. A etapa 1 roda inteira sem credencial da Anthropic.
ANTHROPIC_API_KEY=
```

- [ ] **Step 6: Subir o banco e verificar que os dois bancos e papéis existem**

```bash
docker compose up -d db
```

Esperar o healthcheck e então verificar:

```bash
docker compose exec db psql -U postgres -c "select rolname, rolcanlogin from pg_roles where rolname like 'financeiro%' order by 1"
docker compose exec db psql -U postgres -c "select datname from pg_database where datname like 'financeiro%' order by 1"
```

Expected: `financeiro_app` e `financeiro_etl` com `rolcanlogin = t`; os bancos
`financeiro` e `financeiro_test`.

- [ ] **Step 7: Verificar o `statement_timeout` do papel do agente**

```bash
docker compose exec db psql "postgresql://financeiro_app:financeiro_app@localhost:5432/financeiro" -c "show statement_timeout"
```

Expected: `5s`

- [ ] **Step 8: Commit**

```bash
git add Dockerfile docker-compose.yml .env.example docker
git commit -m "feat: compose com postgres, dois papeis e banco de teste"
```

---

### Task 3: Configuração e conexões por papel

**Files:**
- Create: `src/financeiro/config.py`, `src/financeiro/db/__init__.py`, `src/financeiro/db/connection.py`
- Test: `tests/__init__.py` (já existe), `tests/test_config.py`, `tests/db/__init__.py`, `tests/db/test_connection.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: o PostgreSQL da Task 2.
- Produces: `Settings` (`@dataclass(frozen=True)`) com os campos `owner_url`, `app_url`, `etl_url: str`; `settings_from_env(env: Mapping[str, str] | None = None) -> Settings`; `suite_settings_from_env(env: Mapping[str, str] | None = None) -> Settings`. Em `connection.py`: `connect(url: str) -> psycopg.Connection` e os context managers `owner_connection(settings: Settings)`, `app_connection(settings: Settings)`, `etl_connection(settings: Settings)`.

> **Por que `suite_settings_from_env` e não `test_settings_from_env`:** o pytest
> coleta por padrão toda função que casa com `test*` nos módulos de teste —
> incluindo uma função **importada** para o namespace do módulo. Com o nome
> `test_settings_from_env`, o `tests/test_config.py` que a importa faria o pytest
> tentar coletá-la como teste e errar com `fixture 'env' not found`. O prefixo
> `suite_` evita isso. `testing_` **não** evitaria: `test*` casa com ele também.

- [ ] **Step 1: Escrever o teste de configuração, que falha**

Criar `tests/test_config.py`:

```python
from financeiro.config import Settings, settings_from_env, suite_settings_from_env


def test_defaults_apontam_para_o_compose_local():
    s = settings_from_env({})
    assert s.owner_url.endswith("localhost:5433/financeiro")
    assert "financeiro_app" in s.app_url
    assert "financeiro_etl" in s.etl_url


def test_ambiente_sobrescreve_os_defaults():
    s = settings_from_env(
        {
            "DATABASE_URL_OWNER": "postgresql://o@db:5432/x",
            "DATABASE_URL_APP": "postgresql://a@db:5432/x",
            "DATABASE_URL_ETL": "postgresql://e@db:5432/x",
        }
    )
    assert s == Settings(
        owner_url="postgresql://o@db:5432/x",
        app_url="postgresql://a@db:5432/x",
        etl_url="postgresql://e@db:5432/x",
    )


def test_settings_da_suite_apontam_para_o_banco_de_teste():
    s = suite_settings_from_env({})
    assert s.owner_url.endswith("/financeiro_test")
    assert s.app_url.endswith("/financeiro_test")
    assert s.etl_url.endswith("/financeiro_test")


def test_settings_da_suite_respeitam_o_ambiente():
    s = suite_settings_from_env({"DATABASE_URL_TEST_APP": "postgresql://a@db:5432/t"})
    assert s.app_url == "postgresql://a@db:5432/t"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.config'`

- [ ] **Step 3: Implementar a configuração**

Criar `src/financeiro/config.py`:

```python
"""Leitura das URLs de banco a partir do ambiente.

Os defaults apontam para o docker-compose local, para que `pytest` rode sem
nenhuma variavel de ambiente definida.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

_LOCAL = "localhost:5433"

_DEFAULTS = {
    "DATABASE_URL_OWNER": f"postgresql://postgres:postgres@{_LOCAL}/financeiro",
    "DATABASE_URL_APP": f"postgresql://financeiro_app:financeiro_app@{_LOCAL}/financeiro",
    "DATABASE_URL_ETL": f"postgresql://financeiro_etl:financeiro_etl@{_LOCAL}/financeiro",
    "DATABASE_URL_TEST_OWNER": f"postgresql://postgres:postgres@{_LOCAL}/financeiro_test",
    "DATABASE_URL_TEST_APP": f"postgresql://financeiro_app:financeiro_app@{_LOCAL}/financeiro_test",
    "DATABASE_URL_TEST_ETL": f"postgresql://financeiro_etl:financeiro_etl@{_LOCAL}/financeiro_test",
}


@dataclass(frozen=True)
class Settings:
    owner_url: str
    app_url: str
    etl_url: str


def _build(env: Mapping[str, str] | None, prefix: str) -> Settings:
    source = env if env is not None else os.environ

    def pick(suffix: str) -> str:
        key = f"DATABASE_URL_{prefix}{suffix}"
        return source.get(key) or _DEFAULTS[key]

    return Settings(owner_url=pick("OWNER"), app_url=pick("APP"), etl_url=pick("ETL"))


def settings_from_env(env: Mapping[str, str] | None = None) -> Settings:
    """URLs do banco de desenvolvimento."""
    return _build(env, "")


def suite_settings_from_env(env: Mapping[str, str] | None = None) -> Settings:
    """URLs do banco da suite de testes.

    Nao se chama `test_settings_from_env` de proposito: o pytest coletaria o
    nome `test*` importado num modulo de teste e erraria com fixture inexistente.
    """
    return _build(env, "TEST_")
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS, 4 testes

- [ ] **Step 5: Escrever o `conftest.py` com as fixtures de banco**

Criar `tests/conftest.py`:

```python
"""Fixtures de banco para os testes de integracao.

A suite usa o banco `financeiro_test`, separado do de desenvolvimento, e trunca
as tabelas entre testes. Marque com @pytest.mark.db todo teste que precise dele.
"""

import psycopg
import pytest

from financeiro.config import Settings, suite_settings_from_env


@pytest.fixture(scope="session")
def settings() -> Settings:
    return suite_settings_from_env()


@pytest.fixture(scope="session")
def require_db(settings: Settings) -> None:
    """Pula a suite de integracao com mensagem clara se o banco nao estiver no ar."""
    try:
        with psycopg.connect(settings.owner_url, connect_timeout=3):
            pass
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL indisponivel ({exc}). Rode: docker compose up -d db")
```

- [ ] **Step 6: Criar o pacote de testes de banco**

```bash
mkdir -p tests/db
touch tests/db/__init__.py
```

- [ ] **Step 7: Escrever o teste de conexões, que falha**

Criar `tests/db/test_connection.py`:

```python
import pytest

from financeiro.db.connection import app_connection, etl_connection, owner_connection

pytestmark = pytest.mark.db


def test_owner_conecta_e_e_postgres(require_db, settings):
    with owner_connection(settings) as conn:
        assert conn.execute("select current_user").fetchone()[0] == "postgres"


def test_app_conecta_com_o_papel_do_agente(require_db, settings):
    with app_connection(settings) as conn:
        assert conn.execute("select current_user").fetchone()[0] == "financeiro_app"


def test_etl_conecta_com_o_papel_de_carga(require_db, settings):
    with etl_connection(settings) as conn:
        assert conn.execute("select current_user").fetchone()[0] == "financeiro_etl"


def test_app_tem_statement_timeout_de_cinco_segundos(require_db, settings):
    with app_connection(settings) as conn:
        assert conn.execute("show statement_timeout").fetchone()[0] == "5s"
```

- [ ] **Step 8: Rodar e confirmar que falha**

Run: `python -m pytest tests/db/test_connection.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.db.connection'`

- [ ] **Step 9: Implementar as conexões**

```bash
mkdir -p src/financeiro/db
touch src/financeiro/db/__init__.py
```

Criar `src/financeiro/db/connection.py`:

```python
"""Fabricas de conexao, uma por papel.

O papel existe para que o agente rode numa conexao que fisicamente nao escreve.
Nunca use owner_connection ou etl_connection em codigo do agente.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from financeiro.config import Settings


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url)


@contextmanager
def owner_connection(settings: Settings) -> Iterator[psycopg.Connection]:
    """Dono do banco. Use so para aplicar migrations."""
    with connect(settings.owner_url) as conn:
        yield conn


@contextmanager
def app_connection(settings: Settings) -> Iterator[psycopg.Connection]:
    """Papel do agente: somente SELECT."""
    with connect(settings.app_url) as conn:
        yield conn


@contextmanager
def etl_connection(settings: Settings) -> Iterator[psycopg.Connection]:
    """Papel de carga: seed, importador, categorizacao."""
    with connect(settings.etl_url) as conn:
        yield conn
```

- [ ] **Step 10: Rodar e confirmar que passa**

Run: `docker compose up -d db && python -m pytest tests/db/test_connection.py -v`
Expected: PASS, 4 testes

- [ ] **Step 11: Commit**

```bash
git add src/financeiro/config.py src/financeiro/db tests/conftest.py tests/test_config.py tests/db
git commit -m "feat: configuracao e conexoes por papel de banco"
```

---

### Task 4: Runner de migrations

**Files:**
- Create: `src/financeiro/db/migrate.py`, `src/financeiro/db/migrations/.gitkeep`
- Test: `tests/db/test_migrate.py`

**Interfaces:**
- Consumes: `Settings`, `owner_connection` (Task 3).
- Produces: `MIGRATIONS_DIR: Path`; `discover_migrations(directory: Path | None = None) -> list[tuple[str, Path]]` devolvendo `(versao, caminho)` ordenado por nome; `applied_versions(conn) -> set[str]`; `migrate(conn, directory: Path | None = None) -> list[str]` devolvendo as versões aplicadas **nesta** chamada; CLI `python -m financeiro.db.migrate`.

- [ ] **Step 1: Criar o diretório de migrations**

```bash
mkdir -p src/financeiro/db/migrations
touch src/financeiro/db/migrations/.gitkeep
```

- [ ] **Step 2: Escrever o teste do runner, que falha**

Criar `tests/db/test_migrate.py`:

```python
from contextlib import contextmanager
from pathlib import Path

import pytest

from financeiro.db.connection import owner_connection
from financeiro.db.migrate import applied_versions, discover_migrations, migrate


def test_discover_ordena_por_nome_com_zero_padding(tmp_path: Path):
    for name in ("010_dez.sql", "002_dois.sql", "001_um.sql"):
        (tmp_path / name).write_text("select 1;", encoding="utf-8")
    assert [v for v, _ in discover_migrations(tmp_path)] == ["001", "002", "010"]


def test_discover_ignora_arquivos_que_nao_sao_sql(tmp_path: Path):
    (tmp_path / "001_um.sql").write_text("select 1;", encoding="utf-8")
    (tmp_path / "README.md").write_text("nao sou migration", encoding="utf-8")
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    assert [v for v, _ in discover_migrations(tmp_path)] == ["001"]


@contextmanager
def probe_schema(settings):
    """Conexao num schema descartavel.

    Os testes do runner precisam de um `schema_migrations` virgem. Criar um
    schema proprio e trocar o `search_path` isola isso: sem essa isolacao, o
    teste apagaria o registro da migration 001 real do banco de teste, e a
    fixture `migrated_db` tentaria reaplica-la depois, falhando com
    "relation already exists" — uma dependencia de ordem entre testes.
    """
    with owner_connection(settings) as conn:
        conn.execute("drop schema if exists migrate_probe cascade")
        conn.execute("create schema migrate_probe")
        conn.execute("set search_path to migrate_probe")
        conn.commit()
        try:
            yield conn
        finally:
            conn.execute("set search_path to public")
            conn.execute("drop schema if exists migrate_probe cascade")
            conn.commit()


@pytest.mark.db
def test_migrate_aplica_pendentes_e_registra_versao(require_db, settings, tmp_path: Path):
    (tmp_path / "001_cria_tabela_fake.sql").write_text(
        "create table fake_migrate (id int primary key);", encoding="utf-8"
    )
    with probe_schema(settings) as conn:
        aplicadas = migrate(conn, tmp_path)
        assert aplicadas == ["001"]
        assert applied_versions(conn) == {"001"}
        assert conn.execute("select count(*) from fake_migrate").fetchone()[0] == 0


@pytest.mark.db
def test_migrate_e_idempotente(require_db, settings, tmp_path: Path):
    (tmp_path / "001_cria_tabela_fake.sql").write_text(
        "create table fake_migrate (id int primary key);", encoding="utf-8"
    )
    with probe_schema(settings) as conn:
        assert migrate(conn, tmp_path) == ["001"]
        # Segunda chamada nao reaplica: se reaplicasse, o create table falharia.
        assert migrate(conn, tmp_path) == []
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `python -m pytest tests/db/test_migrate.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.db.migrate'`

- [ ] **Step 4: Implementar o runner**

Criar `src/financeiro/db/migrate.py`:

```python
"""Runner de migrations em SQL puro.

Alembic foi considerado e dispensado: com tres tabelas, o que ele traz e
indirecao entre o revisor e o schema. Aqui o schema se le como SQL.

Convencao: arquivos `NNN_descricao.sql` com N de tres digitos zero-padded, em
`migrations/`. A ordenacao e por nome, entao o zero-padding e obrigatorio.
Cada migration roda na sua propria transacao.
"""

import sys
from pathlib import Path

import psycopg

from financeiro.config import Settings, settings_from_env
from financeiro.db.connection import owner_connection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_CREATE_REGISTRY = """
create table if not exists schema_migrations (
    version    text        primary key,
    applied_at timestamptz not null default now()
)
"""


def discover_migrations(directory: Path | None = None) -> list[tuple[str, Path]]:
    """Devolve (versao, caminho) de cada `.sql`, ordenado por nome do arquivo."""
    target = directory if directory is not None else MIGRATIONS_DIR
    files = sorted(target.glob("*.sql"), key=lambda path: path.name)
    return [(path.name.split("_", 1)[0], path) for path in files]


def applied_versions(conn: psycopg.Connection) -> set[str]:
    conn.execute(_CREATE_REGISTRY)
    conn.commit()
    rows = conn.execute("select version from schema_migrations").fetchall()
    return {row[0] for row in rows}


def migrate(conn: psycopg.Connection, directory: Path | None = None) -> list[str]:
    """Aplica as migrations pendentes. Devolve as versoes aplicadas nesta chamada."""
    already = applied_versions(conn)
    newly_applied: list[str] = []

    for version, path in discover_migrations(directory):
        if version in already:
            continue
        sql = path.read_text(encoding="utf-8")
        conn.execute(sql)
        conn.execute(
            "insert into schema_migrations (version) values (%s)", (version,)
        )
        conn.commit()
        newly_applied.append(version)

    return newly_applied


def main(settings: Settings | None = None) -> int:
    resolved = settings if settings is not None else settings_from_env()
    with owner_connection(resolved) as conn:
        applied = migrate(conn)
    if applied:
        print(f"migrations aplicadas: {', '.join(applied)}")
    else:
        print("nenhuma migration pendente")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `python -m pytest tests/db/test_migrate.py -v`
Expected: PASS, 4 testes

- [ ] **Step 6: Commit**

```bash
git add src/financeiro/db/migrate.py src/financeiro/db/migrations tests/db/test_migrate.py
git commit -m "feat: runner de migrations em sql puro"
```

---

### Task 5: Migration 001 — schema das três tabelas e grants

**Files:**
- Create: `src/financeiro/db/migrations/001_create_finance_tables.sql`
- Test: `tests/db/test_schema.py`

**Interfaces:**
- Consumes: `migrate` (Task 4).
- Produces: as tabelas `accounts`, `categories`, `transactions` com os `check`, as FKs e o `unique (fingerprint)`; `SELECT` para `financeiro_app`; `SELECT/INSERT/UPDATE` e `usage` nas sequences para `financeiro_etl`. Fixture `migrated_db` em `tests/conftest.py`, que aplica as migrations uma vez por sessão e trunca entre testes.

- [ ] **Step 1: Adicionar a fixture `migrated_db` ao `conftest.py`**

Acrescentar ao fim de `tests/conftest.py`:

```python
_TABLES = ("transactions", "categories", "accounts")


@pytest.fixture(scope="session")
def migrated_db(require_db, settings: Settings) -> None:
    """Aplica as migrations no banco de teste, uma vez por sessao."""
    from financeiro.db.migrate import migrate

    with psycopg.connect(settings.owner_url) as conn:
        migrate(conn)


@pytest.fixture
def clean_db(migrated_db, settings: Settings) -> None:
    """Trunca as tabelas de dados antes de cada teste que use o banco."""
    with psycopg.connect(settings.owner_url) as conn:
        conn.execute(
            "truncate table " + ", ".join(_TABLES) + " restart identity cascade"
        )
        conn.commit()
```

- [ ] **Step 2: Escrever o teste de schema, que falha**

Criar `tests/db/test_schema.py`:

```python
import psycopg
import pytest

from financeiro.db.connection import app_connection, etl_connection

pytestmark = pytest.mark.db


def _columns(conn, table: str) -> dict[str, str]:
    rows = conn.execute(
        """
        select column_name, data_type
        from information_schema.columns
        where table_schema = 'public' and table_name = %s
        """,
        (table,),
    ).fetchall()
    return {name: dtype for name, dtype in rows}


def test_as_tres_tabelas_existem_com_as_colunas_da_spec(clean_db, settings):
    with app_connection(settings) as conn:
        assert set(_columns(conn, "accounts")) == {
            "id", "name", "kind", "institution", "created_at",
        }
        assert set(_columns(conn, "categories")) == {"id", "name", "kind"}
        assert set(_columns(conn, "transactions")) == {
            "id", "account_id", "category_id", "occurred_on", "amount",
            "description", "fingerprint", "categorized_by",
            "category_confidence", "created_at",
        }


def test_amount_e_numeric_nao_float(clean_db, settings):
    with app_connection(settings) as conn:
        assert _columns(conn, "transactions")["amount"] == "numeric"


def test_check_de_account_kind_recusa_valor_invalido(clean_db, settings):
    with etl_connection(settings) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "insert into accounts (name, kind) values ('X', 'corrente')"
            )


def test_check_de_category_kind_recusa_valor_invalido(clean_db, settings):
    with etl_connection(settings) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "insert into categories (name, kind) values ('x', 'gasto')"
            )


def test_fingerprint_e_unico(clean_db, settings):
    with etl_connection(settings) as conn:
        conn.execute("insert into accounts (name, kind) values ('C', 'checking')")
        conta = conn.execute("select id from accounts").fetchone()[0]
        insert = """
            insert into transactions
                (account_id, occurred_on, amount, description, fingerprint)
            values (%s, '2026-03-01', -10.00, 'X', 'duplicado')
        """
        conn.execute(insert, (conta,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, (conta,))


def test_fk_de_account_id_recusa_conta_inexistente(clean_db, settings):
    with etl_connection(settings) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                """
                insert into transactions
                    (account_id, occurred_on, amount, description, fingerprint)
                values (99999, '2026-03-01', -1.00, 'X', 'fk')
                """
            )


def test_category_id_aceita_nulo(clean_db, settings):
    with etl_connection(settings) as conn:
        conn.execute("insert into accounts (name, kind) values ('C', 'checking')")
        conta = conn.execute("select id from accounts").fetchone()[0]
        conn.execute(
            """
            insert into transactions
                (account_id, occurred_on, amount, description, fingerprint)
            values (%s, '2026-03-01', -10.00, 'X', 'nulo')
            """,
            (conta,),
        )
        assert (
            conn.execute(
                "select category_id from transactions where fingerprint = 'nulo'"
            ).fetchone()[0]
            is None
        )


def test_papel_do_agente_nao_consegue_escrever(clean_db, settings):
    """A segunda camada do perimetro: permissao do Postgres, nao o parser."""
    with app_connection(settings) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("insert into accounts (name, kind) values ('X', 'checking')")


def test_papel_do_agente_nao_consegue_apagar(clean_db, settings):
    with app_connection(settings) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from transactions")


def test_papel_de_carga_consegue_escrever(clean_db, settings):
    with etl_connection(settings) as conn:
        conn.execute("insert into accounts (name, kind) values ('OK', 'savings')")
        conn.commit()
        assert conn.execute("select count(*) from accounts").fetchone()[0] == 1
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `docker compose up -d db && python -m pytest tests/db/test_schema.py -v`
Expected: FAIL. O diretório `migrations/` só tem o `.gitkeep`, então a fixture
`migrated_db` não aplica nada e as tabelas não existem: os testes de colunas
falham no `assert` (conjunto vazio) e os de inserção com
`psycopg.errors.UndefinedTable`.

- [ ] **Step 4: Escrever a migration**

Criar `src/financeiro/db/migrations/001_create_finance_tables.sql`:

```sql
-- Schema das transacoes financeiras.
--
-- Decisoes documentadas na spec (secao 4):
--  * amount e numeric(12,2), nao centavos inteiros: a aritmetica acontece no
--    Postgres, onde numeric e exato, e centavos fariam o LLM dividir por 100.
--  * amount tem sinal (negativo = saida), e transferencia nao e gasto:
--    gasto e categories.kind = 'expense', nunca amount < 0.
--  * category_id nullable e a feature: NULL significa "ainda nao categorizada".

create table accounts (
    id          bigserial   primary key,
    name        text        not null,
    kind        text        not null check (kind in ('checking', 'savings', 'credit_card')),
    institution text        not null default '',
    created_at  timestamptz not null default now()
);

create unique index accounts_name_key on accounts (name);

create table categories (
    id   bigserial primary key,
    name text      not null unique,
    kind text      not null check (kind in ('expense', 'income', 'transfer'))
);

create table transactions (
    id                  bigserial     primary key,
    account_id          bigint        not null references accounts (id),
    category_id         bigint        null references categories (id),
    occurred_on         date          not null,
    amount              numeric(12,2) not null,
    description         text          not null,
    fingerprint         text          not null unique,
    categorized_by      text          null check (categorized_by in ('llm', 'rule', 'manual')),
    category_confidence real          null,
    created_at          timestamptz   not null default now()
);

create index transactions_occurred_on_idx on transactions (occurred_on);
create index transactions_account_occurred_idx on transactions (account_id, occurred_on);
create index transactions_category_idx on transactions (category_id);

-- Grants. Toda migration que cria tabela repete este bloco: `on all tables`
-- vale para as tabelas existentes no momento do grant, nao para as futuras.
grant select on all tables in schema public to financeiro_app;
grant select, insert, update on all tables in schema public to financeiro_etl;
grant usage, select on all sequences in schema public to financeiro_etl;
```

- [ ] **Step 5: Aplicar as migrations nos dois bancos**

```bash
docker compose up -d db
python -m financeiro.db.migrate
DATABASE_URL_OWNER=postgresql://postgres:postgres@localhost:5433/financeiro_test python -m financeiro.db.migrate
```

Expected: `migrations aplicadas: 001` nas duas vezes.

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `python -m pytest tests/db/test_schema.py -v`
Expected: PASS, 10 testes

- [ ] **Step 7: Rodar a suíte completa**

Run: `python -m pytest -v`
Expected: PASS, todos

- [ ] **Step 8: Commit**

```bash
git add src/financeiro/db/migrations/001_create_finance_tables.sql tests/conftest.py tests/db/test_schema.py
git commit -m "feat: schema das tres tabelas e grants por papel"
```

---

### Task 6: Seed — dados de referência e lista de meses

**Files:**
- Create: `src/financeiro/seed/__init__.py`, `src/financeiro/seed/reference.py`
- Test: `tests/seed/__init__.py`, `tests/seed/test_reference.py`

**Interfaces:**
- Consumes: `Account`, `AccountKind`, `Category`, `CategoryKind` (Task 1).
- Produces: `SEED_RANDOM_SEED: int = 42`; `SEED_REFERENCE_DATE: date = date(2026, 12, 31)`; `SEED_MONTHS: int = 12`; `CHECKING: str`, `CREDIT_CARD: str`, `SAVINGS: str` (nomes das contas); `ACCOUNTS: tuple[Account, ...]`; `CATEGORIES: tuple[Category, ...]`; `CATEGORY_KINDS: dict[str, CategoryKind]`; `seed_months() -> list[date]` devolvendo o primeiro dia de cada um dos 12 meses, em ordem crescente; `day_in(month: date, day: int) -> date` que trunca o dia ao último dia do mês.

- [ ] **Step 1: Criar os pacotes**

```bash
mkdir -p src/financeiro/seed tests/seed
touch src/financeiro/seed/__init__.py tests/seed/__init__.py
```

- [ ] **Step 2: Escrever o teste, que falha**

Criar `tests/seed/test_reference.py`:

```python
from datetime import date

from financeiro.domain.models import AccountKind, CategoryKind
from financeiro.seed.reference import (
    ACCOUNTS,
    CATEGORIES,
    CATEGORY_KINDS,
    CHECKING,
    CREDIT_CARD,
    SAVINGS,
    SEED_MONTHS,
    SEED_RANDOM_SEED,
    SEED_REFERENCE_DATE,
    day_in,
    seed_months,
)


def test_constantes_sao_as_da_spec():
    assert SEED_RANDOM_SEED == 42
    assert SEED_REFERENCE_DATE == date(2026, 12, 31)
    assert SEED_MONTHS == 12


def test_tres_contas_uma_de_cada_tipo():
    assert len(ACCOUNTS) == 3
    assert {a.kind for a in ACCOUNTS} == {
        AccountKind.CHECKING,
        AccountKind.CREDIT_CARD,
        AccountKind.SAVINGS,
    }
    assert {a.name for a in ACCOUNTS} == {CHECKING, CREDIT_CARD, SAVINGS}


def test_nomes_de_conta_sao_unicos():
    assert len({a.name for a in ACCOUNTS}) == len(ACCOUNTS)


def test_nomes_de_categoria_sao_unicos():
    assert len({c.name for c in CATEGORIES}) == len(CATEGORIES)


def test_existe_categoria_de_cada_kind():
    assert {c.kind for c in CATEGORIES} == {
        CategoryKind.EXPENSE,
        CategoryKind.INCOME,
        CategoryKind.TRANSFER,
    }


def test_category_kinds_cobre_todas_as_categorias():
    assert set(CATEGORY_KINDS) == {c.name for c in CATEGORIES}
    for categoria in CATEGORIES:
        assert CATEGORY_KINDS[categoria.name] is categoria.kind


def test_seed_months_devolve_doze_meses_contiguos_terminando_na_referencia():
    meses = seed_months()
    assert len(meses) == 12
    assert meses[0] == date(2026, 1, 1)
    assert meses[-1] == date(2026, 12, 1)
    assert meses == sorted(meses)
    for anterior, seguinte in zip(meses, meses[1:]):
        esperado_mes = anterior.month + 1
        assert (seguinte.year, seguinte.month) == (anterior.year, esperado_mes)


def test_day_in_monta_a_data_dentro_do_mes():
    assert day_in(date(2026, 3, 1), 14) == date(2026, 3, 14)


def test_day_in_trunca_dia_que_nao_existe_no_mes():
    assert day_in(date(2026, 2, 1), 30) == date(2026, 2, 28)
    assert day_in(date(2026, 4, 1), 31) == date(2026, 4, 30)
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `python -m pytest tests/seed/test_reference.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.seed.reference'`

- [ ] **Step 4: Implementar os dados de referência**

Criar `src/financeiro/seed/reference.py`:

```python
"""Constantes e dados de referencia do seed sintetico.

A data de referencia e FIXA, nunca date.today(). Com `today`, a pergunta
"quanto gastei em outubro?" teria uma resposta em novembro e outra em janeiro, e
as queries de ground truth dos evals ficariam erradas sozinhas.
"""

import calendar
from datetime import date

from financeiro.domain.models import Account, AccountKind, Category, CategoryKind

SEED_RANDOM_SEED = 42
SEED_REFERENCE_DATE = date(2026, 12, 31)
SEED_MONTHS = 12

CHECKING = "Conta Corrente Nubank"
CREDIT_CARD = "Cartao de Credito Nubank"
SAVINGS = "Poupanca Caixa"

ACCOUNTS: tuple[Account, ...] = (
    Account(name=CHECKING, kind=AccountKind.CHECKING, institution="Nubank"),
    Account(name=CREDIT_CARD, kind=AccountKind.CREDIT_CARD, institution="Nubank"),
    Account(name=SAVINGS, kind=AccountKind.SAVINGS, institution="Caixa"),
)

CATEGORIES: tuple[Category, ...] = (
    Category(name="alimentacao", kind=CategoryKind.EXPENSE),
    Category(name="moradia", kind=CategoryKind.EXPENSE),
    Category(name="transporte", kind=CategoryKind.EXPENSE),
    Category(name="saude", kind=CategoryKind.EXPENSE),
    Category(name="educacao", kind=CategoryKind.EXPENSE),
    Category(name="lazer", kind=CategoryKind.EXPENSE),
    Category(name="vestuario", kind=CategoryKind.EXPENSE),
    Category(name="servicos", kind=CategoryKind.EXPENSE),
    Category(name="outros", kind=CategoryKind.EXPENSE),
    Category(name="salario", kind=CategoryKind.INCOME),
    Category(name="renda-extra", kind=CategoryKind.INCOME),
    Category(name="pagamento-fatura", kind=CategoryKind.TRANSFER),
    Category(name="aporte-poupanca", kind=CategoryKind.TRANSFER),
)

CATEGORY_KINDS: dict[str, CategoryKind] = {c.name: c.kind for c in CATEGORIES}


def seed_months() -> list[date]:
    """Primeiro dia de cada um dos SEED_MONTHS meses que terminam na referencia."""
    meses: list[date] = []
    ano, mes = SEED_REFERENCE_DATE.year, SEED_REFERENCE_DATE.month
    for _ in range(SEED_MONTHS):
        meses.append(date(ano, mes, 1))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return sorted(meses)


def day_in(month: date, day: int) -> date:
    """Data `day` dentro de `month`, truncada ao ultimo dia quando nao existe."""
    ultimo = calendar.monthrange(month.year, month.month)[1]
    return date(month.year, month.month, min(day, ultimo))
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `python -m pytest tests/seed/test_reference.py -v`
Expected: PASS, 9 testes

- [ ] **Step 6: Commit**

```bash
git add src/financeiro/seed tests/seed
git commit -m "feat: dados de referencia e meses do seed"
```

---

### Task 7: Seed — transações recorrentes

**Files:**
- Create: `src/financeiro/seed/recurring.py`
- Test: `tests/seed/test_recurring.py`

**Interfaces:**
- Consumes: `Transaction` (Task 1); `CHECKING`, `CREDIT_CARD`, `seed_months`, `day_in` (Task 6).
- Produces: `RECURRING: tuple[RecurringRule, ...]` e `build_recurring() -> list[Transaction]`. `RecurringRule` é `@dataclass(frozen=True)` com os campos `account_name: str`, `day: int`, `amount: Decimal`, `description: str`, `category_name: str`. Função pura: sem `rng`, porque valor e dia são fixos.

- [ ] **Step 1: Escrever o teste, que falha**

Criar `tests/seed/test_recurring.py`:

```python
from collections import Counter
from decimal import Decimal

from financeiro.seed.recurring import RECURRING, build_recurring
from financeiro.seed.reference import CATEGORY_KINDS, CHECKING, seed_months


def test_cada_regra_gera_uma_transacao_por_mes():
    transacoes = build_recurring()
    assert len(transacoes) == len(RECURRING) * len(seed_months())


def test_cada_regra_aparece_doze_vezes():
    contagem = Counter(t.description for t in build_recurring())
    for regra in RECURRING:
        assert contagem[regra.description] == 12


def test_salario_e_entrada_de_sete_mil_e_quinhentos_na_corrente():
    salarios = [t for t in build_recurring() if t.category_name == "salario"]
    assert len(salarios) == 12
    for t in salarios:
        assert t.amount == Decimal("7500.00")
        assert t.account_name == CHECKING
        assert t.occurred_on.day == 5


def test_aluguel_e_saida_fixa_no_dia_dez():
    alugueis = [t for t in build_recurring() if t.description.startswith("ALUGUEL")]
    assert len(alugueis) == 12
    for t in alugueis:
        assert t.amount == Decimal("-2200.00")
        assert t.occurred_on.day == 10
        assert t.category_name == "moradia"


def test_todas_as_categorias_usadas_existem_na_referencia():
    for t in build_recurring():
        assert t.category_name in CATEGORY_KINDS


def test_entradas_sao_positivas_e_saidas_negativas():
    for t in build_recurring():
        if CATEGORY_KINDS[t.category_name] == "income":
            assert t.amount > 0
        else:
            assert t.amount < 0


def test_e_deterministico():
    assert build_recurring() == build_recurring()


def test_cobre_os_doze_meses_sem_buraco():
    meses = {(t.occurred_on.year, t.occurred_on.month) for t in build_recurring()}
    assert len(meses) == 12
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/seed/test_recurring.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.seed.recurring'`

- [ ] **Step 3: Implementar as recorrentes**

Criar `src/financeiro/seed/recurring.py`:

```python
"""Transacoes de dia e valor fixos: salario, aluguel, assinaturas.

Cada regra existe para habilitar uma classe de pergunta nos evals: o salario
habilita "qual minha renda mensal" e "quanto sobrou"; o aluguel e as
assinaturas habilitam "qual meu custo fixo".
"""

from dataclasses import dataclass
from decimal import Decimal

from financeiro.domain.models import Transaction
from financeiro.seed.reference import CHECKING, CREDIT_CARD, day_in, seed_months


@dataclass(frozen=True)
class RecurringRule:
    account_name: str
    day: int
    amount: Decimal
    description: str
    category_name: str


RECURRING: tuple[RecurringRule, ...] = (
    RecurringRule(CHECKING, 5, Decimal("7500.00"), "TED RECEBIDA EMPRESA XYZ", "salario"),
    RecurringRule(CHECKING, 10, Decimal("-2200.00"), "ALUGUEL IMOB SANTOS", "moradia"),
    RecurringRule(CHECKING, 13, Decimal("-129.90"), "VIVO FIBRA 400MB", "servicos"),
    RecurringRule(CHECKING, 18, Decimal("-1150.00"), "FACULDADE MENSALIDADE", "educacao"),
    RecurringRule(CREDIT_CARD, 8, Decimal("-119.90"), "SMARTFIT *MENSALIDADE", "saude"),
    RecurringRule(CREDIT_CARD, 20, Decimal("-55.90"), "NETFLIX.COM", "lazer"),
)


def build_recurring() -> list[Transaction]:
    """Uma transacao por regra por mes. Pura e deterministica: nada sorteado."""
    transacoes: list[Transaction] = []
    for mes in seed_months():
        for regra in RECURRING:
            transacoes.append(
                Transaction(
                    account_name=regra.account_name,
                    occurred_on=day_in(mes, regra.day),
                    amount=regra.amount,
                    description=regra.description,
                    category_name=regra.category_name,
                )
            )
    return transacoes
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/seed/test_recurring.py -v`
Expected: PASS, 8 testes

- [ ] **Step 5: Commit**

```bash
git add src/financeiro/seed/recurring.py tests/seed/test_recurring.py
git commit -m "feat: transacoes recorrentes do seed"
```

---

### Task 8: Seed — transações variáveis, tendência, anomalia e férias

**Files:**
- Create: `src/financeiro/seed/variable.py`
- Test: `tests/seed/test_variable.py`

**Interfaces:**
- Consumes: `Transaction` (Task 1); `CHECKING`, `CREDIT_CARD`, `seed_months`, `day_in` (Task 6).
- Produces: `TREND_CATEGORY: str = "alimentacao"`; `TREND_MONTHS: int = 3`; `TREND_FACTOR: Decimal`; `ANOMALY_MONTH: date`; `ANOMALY_AMOUNT: Decimal`; `VACATION_MONTH: date`; `build_variable(rng: random.Random) -> list[Transaction]`. Recebe o `rng` por parâmetro — nunca cria o seu, nunca usa o módulo `random` global.

- [ ] **Step 1: Escrever o teste, que falha**

Criar `tests/seed/test_variable.py`:

```python
import random
from datetime import date
from decimal import Decimal

from financeiro.seed.reference import CATEGORY_KINDS, SEED_RANDOM_SEED, seed_months
from financeiro.seed.variable import (
    ANOMALY_AMOUNT,
    ANOMALY_MONTH,
    TREND_CATEGORY,
    TREND_MONTHS,
    VACATION_MONTH,
    build_variable,
)


def rng() -> random.Random:
    return random.Random(SEED_RANDOM_SEED)


def total_do_mes(transacoes, categoria: str, mes: date) -> Decimal:
    return sum(
        (
            -t.amount
            for t in transacoes
            if t.category_name == categoria
            and (t.occurred_on.year, t.occurred_on.month) == (mes.year, mes.month)
        ),
        Decimal("0"),
    )


def test_e_deterministico_para_o_mesmo_seed():
    assert build_variable(rng()) == build_variable(rng())


def test_seeds_diferentes_produzem_dados_diferentes():
    assert build_variable(random.Random(1)) != build_variable(random.Random(2))


def test_todas_as_categorias_existem_na_referencia():
    for t in build_variable(rng()):
        assert t.category_name in CATEGORY_KINDS


def test_todas_as_variaveis_sao_saidas():
    for t in build_variable(rng()):
        assert t.amount < 0
        assert CATEGORY_KINDS[t.category_name] == "expense"


def test_cobre_os_doze_meses():
    transacoes = build_variable(rng())
    meses = {(t.occurred_on.year, t.occurred_on.month) for t in transacoes}
    assert len(meses) == 12


def test_tendencia_de_alta_plantada_nos_ultimos_tres_meses():
    """Sem esta tendencia, "qual categoria cresceu mais" responderia ruido."""
    transacoes = build_variable(rng())
    meses = seed_months()
    antigos = meses[:-TREND_MONTHS]
    recentes = meses[-TREND_MONTHS:]

    media_antiga = sum(
        (total_do_mes(transacoes, TREND_CATEGORY, m) for m in antigos), Decimal("0")
    ) / len(antigos)
    media_recente = sum(
        (total_do_mes(transacoes, TREND_CATEGORY, m) for m in recentes), Decimal("0")
    ) / len(recentes)

    assert media_recente > media_antiga * Decimal("1.3")


def test_existe_exatamente_uma_anomalia_de_saude():
    transacoes = build_variable(rng())
    anomalias = [t for t in transacoes if t.amount == ANOMALY_AMOUNT]
    assert len(anomalias) == 1
    anomalia = anomalias[0]
    assert anomalia.category_name == "saude"
    assert (anomalia.occurred_on.year, anomalia.occurred_on.month) == (
        ANOMALY_MONTH.year,
        ANOMALY_MONTH.month,
    )


def test_anomalia_e_o_maior_gasto_do_ano():
    transacoes = build_variable(rng())
    assert min(t.amount for t in transacoes) == ANOMALY_AMOUNT


def test_mes_de_ferias_tem_lazer_muito_acima_da_media():
    transacoes = build_variable(rng())
    outros_meses = [m for m in seed_months() if m != VACATION_MONTH]
    media_normal = sum(
        (total_do_mes(transacoes, "lazer", m) for m in outros_meses), Decimal("0")
    ) / len(outros_meses)
    ferias = total_do_mes(transacoes, "lazer", VACATION_MONTH)
    assert ferias > media_normal * Decimal("2")


def test_descricoes_sao_sujas_como_extrato_real():
    """Descricao limpa tornaria a categorizacao da etapa 3 trivial."""
    descricoes = {t.description for t in build_variable(rng())}
    assert any("*" in d for d in descricoes)
    assert all(d == d.upper() for d in descricoes)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/seed/test_variable.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.seed.variable'`

- [ ] **Step 3: Implementar as variáveis**

Criar `src/financeiro/seed/variable.py`:

```python
"""Transacoes de valor e frequencia variaveis, com padroes plantados.

Cada padrao existe para habilitar uma classe de pergunta nos evals. Dados
uniformemente aleatorios tornariam "qual categoria cresceu mais?" uma pergunta
sem resposta verdadeira, e tanto o agente quanto o ground truth "acertariam"
ruido.

As descricoes sao sujas de proposito: abreviadas, em caixa alta, com prefixo de
adquirente. Descricao limpa tornaria a categorizacao da etapa 3 trivial e sua
metrica sem significado.
"""

import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from financeiro.domain.models import Transaction
from financeiro.seed.reference import CHECKING, CREDIT_CARD, day_in, seed_months

TREND_CATEGORY = "alimentacao"
TREND_MONTHS = 3
TREND_FACTOR = Decimal("1.7")

ANOMALY_MONTH = date(2026, 6, 1)
ANOMALY_AMOUNT = Decimal("-4200.00")
ANOMALY_DESCRIPTION = "HOSP SAO LUCAS INTERNAC"

VACATION_MONTH = date(2026, 7, 1)
VACATION_FACTOR = Decimal("3.0")


@dataclass(frozen=True)
class VariableRule:
    account_name: str
    category_name: str
    min_count: int
    max_count: int
    min_amount: int
    max_amount: int
    descriptions: tuple[str, ...]


VARIABLE: tuple[VariableRule, ...] = (
    VariableRule(
        CREDIT_CARD, "alimentacao", 4, 6, 80, 350,
        ("SUPERM BOM PRECO LT", "PAG*MERCADOLIVRE", "HORTIFRUTI DA ESQ", "ATACADAO FL 12"),
    ),
    VariableRule(
        CREDIT_CARD, "transporte", 6, 12, 12, 45,
        ("UBER *TRIP", "99*VIAGEM", "POSTO IPIRANGA AV B"),
    ),
    VariableRule(
        CREDIT_CARD, "lazer", 2, 5, 25, 180,
        ("IFD*IFOOD", "CINEMARK SHOP SUL", "SPOTIFY BR", "BAR DO ZE LTDA"),
    ),
    VariableRule(
        CHECKING, "saude", 0, 2, 20, 160,
        ("DROGASIL 1234", "FARMACIA PAGUE MEN"),
    ),
    VariableRule(
        CHECKING, "vestuario", 0, 2, 60, 420,
        ("RIACHUELO FL 308", "RENNER SHOP SUL"),
    ),
    VariableRule(
        CHECKING, "outros", 1, 3, 15, 200,
        ("PIX ENVIADO JOAO M S", "SAQUE 24H BANCO24HORAS", "TARIFA PACOTE SERV"),
    ),
)


def _factor_for(rule: VariableRule, month: date, recent: set[date]) -> Decimal:
    factor = Decimal("1")
    if rule.category_name == TREND_CATEGORY and month in recent:
        factor *= TREND_FACTOR
    if month == VACATION_MONTH and rule.category_name in ("lazer", "transporte"):
        factor *= VACATION_FACTOR
    return factor


def build_variable(rng: random.Random) -> list[Transaction]:
    """Transacoes variaveis dos 12 meses.

    O `rng` vem por parametro de proposito: o chamador controla a semente, e
    nenhum modulo do seed toca no `random` global.
    """
    meses = seed_months()
    recentes = set(meses[-TREND_MONTHS:])
    transacoes: list[Transaction] = []

    for mes in meses:
        for regra in VARIABLE:
            fator = _factor_for(regra, mes, recentes)
            quantidade = rng.randint(regra.min_count, regra.max_count)
            if mes == VACATION_MONTH and regra.category_name in ("lazer", "transporte"):
                quantidade += 3
            for _ in range(quantidade):
                centavos = rng.randint(regra.min_amount * 100, regra.max_amount * 100)
                valor = (Decimal(centavos) / Decimal(100) * fator).quantize(
                    Decimal("0.01")
                )
                transacoes.append(
                    Transaction(
                        account_name=regra.account_name,
                        occurred_on=day_in(mes, rng.randint(1, 28)),
                        amount=-valor,
                        description=rng.choice(regra.descriptions),
                        category_name=regra.category_name,
                    )
                )

    transacoes.append(
        Transaction(
            account_name=CHECKING,
            occurred_on=day_in(ANOMALY_MONTH, 17),
            amount=ANOMALY_AMOUNT,
            description=ANOMALY_DESCRIPTION,
            category_name="saude",
        )
    )
    return transacoes
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/seed/test_variable.py -v`
Expected: PASS, 10 testes

> Se `test_anomalia_e_o_maior_gasto_do_ano` falhar, um valor variável passou de
> R$ 4.200 — reduza `max_amount` da regra culpada ou aumente `ANOMALY_AMOUNT`.
> Não remova o teste: ele é o que garante que a pergunta "qual meu maior gasto
> do ano?" tem uma resposta única.

- [ ] **Step 5: Commit**

```bash
git add src/financeiro/seed/variable.py tests/seed/test_variable.py
git commit -m "feat: transacoes variaveis com tendencia, anomalia e ferias"
```

---

### Task 9: Seed — transferências e a armadilha da contagem dupla

**Files:**
- Create: `src/financeiro/seed/transfers.py`
- Test: `tests/seed/test_transfers.py`

**Interfaces:**
- Consumes: `Transaction`, `CategoryKind` (Task 1); `CHECKING`, `CREDIT_CARD`, `CATEGORY_KINDS`, `seed_months`, `day_in` (Task 6).
- Produces: `STATEMENT_PAYMENT_DAY: int = 10`; `SAVINGS_DEPOSIT_DAY: int = 25`; `SAVINGS_DEPOSIT_AMOUNT: Decimal`; `build_transfers(transactions: Sequence[Transaction]) -> list[Transaction]`. Recebe as transações já geradas (recorrentes + variáveis) porque o pagamento de fatura é calculado a partir das compras do cartão.

- [ ] **Step 1: Escrever o teste, que falha**

Criar `tests/seed/test_transfers.py`:

```python
import random
from datetime import date
from decimal import Decimal

from financeiro.seed.recurring import build_recurring
from financeiro.seed.reference import (
    CATEGORY_KINDS,
    CHECKING,
    CREDIT_CARD,
    SEED_RANDOM_SEED,
    seed_months,
)
from financeiro.seed.transfers import (
    SAVINGS_DEPOSIT_AMOUNT,
    SAVINGS_DEPOSIT_DAY,
    STATEMENT_PAYMENT_DAY,
    build_transfers,
)
from financeiro.seed.variable import build_variable


def base() -> list:
    return build_recurring() + build_variable(random.Random(SEED_RANDOM_SEED))


def test_pagamento_de_fatura_existe_de_fevereiro_a_dezembro():
    transferencias = build_transfers(base())
    pagamentos = [t for t in transferencias if t.category_name == "pagamento-fatura"]
    assert len(pagamentos) == 11
    meses = sorted((t.occurred_on.year, t.occurred_on.month) for t in pagamentos)
    assert meses == [(2026, m) for m in range(2, 13)]


def test_pagamento_cai_no_dia_configurado_e_sai_da_corrente():
    for t in build_transfers(base()):
        if t.category_name == "pagamento-fatura":
            assert t.occurred_on.day == STATEMENT_PAYMENT_DAY
            assert t.account_name == CHECKING
            assert t.amount < 0


def test_cada_pagamento_casa_com_as_compras_do_cartao_no_mes_anterior():
    """Teste central: garante que a armadilha da contagem dupla esta plantada."""
    transacoes = base()
    pagamentos = {
        (t.occurred_on.year, t.occurred_on.month): t.amount
        for t in build_transfers(transacoes)
        if t.category_name == "pagamento-fatura"
    }
    meses = seed_months()
    for anterior, pagamento_mes in zip(meses, meses[1:]):
        compras = sum(
            (
                t.amount
                for t in transacoes
                if t.account_name == CREDIT_CARD
                and (t.occurred_on.year, t.occurred_on.month)
                == (anterior.year, anterior.month)
            ),
            Decimal("0"),
        )
        assert pagamentos[(pagamento_mes.year, pagamento_mes.month)] == compras


def test_aporte_na_poupanca_todo_mes():
    aportes = [
        t for t in build_transfers(base()) if t.category_name == "aporte-poupanca"
    ]
    assert len(aportes) == 12
    for t in aportes:
        assert t.amount == SAVINGS_DEPOSIT_AMOUNT
        assert t.occurred_on.day == SAVINGS_DEPOSIT_DAY
        assert t.account_name == CHECKING


def test_toda_transferencia_tem_kind_transfer():
    for t in build_transfers(base()):
        assert CATEGORY_KINDS[t.category_name] == "transfer"


def test_somar_negativos_superestima_o_gasto():
    """A armadilha: somar tudo que e negativo conta a fatura duas vezes."""
    transacoes = base()
    transferencias = build_transfers(transacoes)
    todas = transacoes + transferencias

    soma_dos_negativos = -sum(
        (t.amount for t in todas if t.amount < 0), Decimal("0")
    )
    gasto_de_verdade = -sum(
        (
            t.amount
            for t in todas
            if t.amount < 0 and CATEGORY_KINDS[t.category_name] == "expense"
        ),
        Decimal("0"),
    )
    assert soma_dos_negativos > gasto_de_verdade


def test_e_deterministico():
    transacoes = base()
    assert build_transfers(transacoes) == build_transfers(transacoes)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/seed/test_transfers.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.seed.transfers'`

- [ ] **Step 3: Implementar as transferências**

Criar `src/financeiro/seed/transfers.py`:

```python
"""Transferencias: pagamento da fatura do cartao e aporte na poupanca.

Estas transacoes existem para plantar a armadilha da contagem dupla, que e o
problema mais serio de modelagem de financas pessoais:

    Uma compra de R$ 200 no cartao, mais o pagamento da fatura de R$ 200
    debitado da corrente, somam R$ 400 em valores negativos. O gasto foi R$ 200.

Por isso gasto e `categories.kind = 'expense'`, nunca "amount negativo". O
conjunto de evals inclui perguntas desenhadas para pegar esse erro.

Modelo de lancamento unico, nao partidas dobradas: o pagamento e uma linha so,
na conta corrente. As compras do cartao ficam como estao.
"""

from collections.abc import Sequence
from collections import defaultdict
from decimal import Decimal

from financeiro.domain.models import Transaction
from financeiro.seed.reference import CHECKING, CREDIT_CARD, day_in, seed_months

STATEMENT_PAYMENT_DAY = 10
STATEMENT_PAYMENT_DESCRIPTION = "PAGAMENTO FATURA CARTAO"

SAVINGS_DEPOSIT_DAY = 25
SAVINGS_DEPOSIT_AMOUNT = Decimal("-500.00")
SAVINGS_DEPOSIT_DESCRIPTION = "TED POUPANCA CAIXA"


def build_transfers(transactions: Sequence[Transaction]) -> list[Transaction]:
    """Pagamentos de fatura e aportes, derivados das transacoes ja geradas.

    A fatura do mes M cobre as compras do cartao em M-1 e e paga no dia
    STATEMENT_PAYMENT_DAY de M. Janeiro nao tem pagamento: nao existe mes
    anterior na janela.
    """
    por_mes: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
    for t in transactions:
        if t.account_name == CREDIT_CARD:
            por_mes[(t.occurred_on.year, t.occurred_on.month)] += t.amount

    meses = seed_months()
    transferencias: list[Transaction] = []

    for anterior, mes_do_pagamento in zip(meses, meses[1:]):
        total = por_mes[(anterior.year, anterior.month)]
        if total == 0:
            continue
        transferencias.append(
            Transaction(
                account_name=CHECKING,
                occurred_on=day_in(mes_do_pagamento, STATEMENT_PAYMENT_DAY),
                amount=total,
                description=STATEMENT_PAYMENT_DESCRIPTION,
                category_name="pagamento-fatura",
            )
        )

    for mes in meses:
        transferencias.append(
            Transaction(
                account_name=CHECKING,
                occurred_on=day_in(mes, SAVINGS_DEPOSIT_DAY),
                amount=SAVINGS_DEPOSIT_AMOUNT,
                description=SAVINGS_DEPOSIT_DESCRIPTION,
                category_name="aporte-poupanca",
            )
        )

    return transferencias
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/seed/test_transfers.py -v`
Expected: PASS, 7 testes

- [ ] **Step 5: Commit**

```bash
git add src/financeiro/seed/transfers.py tests/seed/test_transfers.py
git commit -m "feat: transferencias e a armadilha da contagem dupla"
```

---

### Task 10: Seed — orquestração, fração sem categoria e determinismo

**Files:**
- Create: `src/financeiro/seed/generator.py`
- Test: `tests/seed/test_generator.py`

**Interfaces:**
- Consumes: tudo das Tasks 6 a 9; `transaction_fingerprint` (Task 1).
- Produces: `UNCATEGORIZED_FRACTION: float = 0.15`; `Dataset` (`@dataclass(frozen=True)`) com `accounts: tuple[Account, ...]`, `categories: tuple[Category, ...]`, `transactions: tuple[Transaction, ...]`; `build_dataset(rng: random.Random | None = None) -> Dataset`; `fingerprints(dataset: Dataset) -> set[str]`.

- [ ] **Step 1: Escrever o teste, que falha**

Criar `tests/seed/test_generator.py`:

```python
import random
from decimal import Decimal

from financeiro.seed.generator import (
    UNCATEGORIZED_FRACTION,
    build_dataset,
    fingerprints,
)
from financeiro.seed.reference import (
    ACCOUNTS,
    CATEGORIES,
    CATEGORY_KINDS,
    SEED_RANDOM_SEED,
)


def test_e_deterministico_sem_argumento():
    """A propriedade mais importante: sem ela o ground truth dos evals apodrece."""
    assert fingerprints(build_dataset()) == fingerprints(build_dataset())


def test_e_deterministico_com_o_mesmo_seed():
    a = build_dataset(random.Random(SEED_RANDOM_SEED))
    b = build_dataset(random.Random(SEED_RANDOM_SEED))
    assert a == b


def test_seed_diferente_muda_os_dados():
    a = fingerprints(build_dataset(random.Random(1)))
    b = fingerprints(build_dataset(random.Random(2)))
    assert a != b


def test_leva_as_contas_e_categorias_de_referencia():
    dataset = build_dataset()
    assert dataset.accounts == ACCOUNTS
    assert dataset.categories == CATEGORIES


def test_volume_na_ordem_de_mil_e_quinhentas_transacoes():
    total = len(build_dataset().transactions)
    assert 900 <= total <= 2200, f"volume fora da faixa util: {total}"


def test_fingerprints_sao_unicos():
    dataset = build_dataset()
    assert len(fingerprints(dataset)) == len(dataset.transactions)


def test_fracao_sem_categoria_fica_entre_dez_e_vinte_por_cento():
    dataset = build_dataset()
    sem_categoria = [t for t in dataset.transactions if t.category_name is None]
    fracao = len(sem_categoria) / len(dataset.transactions)
    assert 0.10 <= fracao <= 0.20, f"fracao fora da faixa: {fracao:.3f}"
    assert abs(fracao - UNCATEGORIZED_FRACTION) < 0.03


def test_nenhuma_transferencia_fica_sem_categoria():
    """Transferencia sem categoria quebraria a armadilha da contagem dupla."""
    for t in build_dataset().transactions:
        if t.description.startswith(("PAGAMENTO FATURA", "TED POUPANCA")):
            assert t.category_name is not None, f"transferencia sem categoria: {t}"
            assert CATEGORY_KINDS[t.category_name] == "transfer"


def test_nenhuma_entrada_fica_sem_categoria():
    for t in build_dataset().transactions:
        if t.description.startswith("TED RECEBIDA"):
            assert t.category_name is not None


def test_cobre_os_doze_meses_sem_buraco():
    meses = {
        (t.occurred_on.year, t.occurred_on.month)
        for t in build_dataset().transactions
    }
    assert len(meses) == 12


def test_toda_transacao_referencia_uma_conta_existente():
    nomes = {a.name for a in ACCOUNTS}
    for t in build_dataset().transactions:
        assert t.account_name in nomes


def test_toda_categoria_usada_existe_na_referencia():
    for t in build_dataset().transactions:
        if t.category_name is not None:
            assert t.category_name in CATEGORY_KINDS


def test_todo_valor_e_decimal_com_duas_casas():
    for t in build_dataset().transactions:
        assert isinstance(t.amount, Decimal)
        assert t.amount == t.amount.quantize(Decimal("0.01"))
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/seed/test_generator.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.seed.generator'`

- [ ] **Step 3: Implementar o gerador**

Criar `src/financeiro/seed/generator.py`:

```python
"""Orquestracao do seed: build_dataset e PURA, nao toca no banco.

Essa escolha e deliberada. Com o gerador puro, as propriedades que importam
(determinismo, tendencia plantada, fatura que casa com o cartao, fracao sem
categoria) sao testadas em milissegundos, sem PostgreSQL. So a persistencia,
em writer.py, precisa de banco.
"""

import random
from dataclasses import dataclass, replace

from financeiro.domain.fingerprint import transaction_fingerprint
from financeiro.domain.models import Account, Category, Transaction
from financeiro.seed.recurring import build_recurring
from financeiro.seed.reference import (
    ACCOUNTS,
    CATEGORIES,
    CATEGORY_KINDS,
    SEED_RANDOM_SEED,
)
from financeiro.seed.transfers import build_transfers
from financeiro.seed.variable import build_variable

UNCATEGORIZED_FRACTION = 0.15


@dataclass(frozen=True)
class Dataset:
    accounts: tuple[Account, ...]
    categories: tuple[Category, ...]
    transactions: tuple[Transaction, ...]


def _sort_key(transaction: Transaction) -> tuple:
    return (
        transaction.occurred_on,
        transaction.account_name,
        str(transaction.amount),
        transaction.description,
    )


def _drop_some_categories(
    transactions: list[Transaction], rng: random.Random
) -> list[Transaction]:
    """Zera a categoria de UNCATEGORIZED_FRACTION das transacoes.

    So despesas variaveis sao elegiveis: transferencia sem categoria quebraria a
    armadilha da contagem dupla, e entrada sem categoria nao acontece num
    extrato real (salario se reconhece pela descricao).
    """
    eligible = [
        index
        for index, t in enumerate(transactions)
        if t.category_name is not None
        and CATEGORY_KINDS[t.category_name] == "expense"
        and not t.description.startswith(("ALUGUEL", "FACULDADE", "VIVO", "SMARTFIT", "NETFLIX"))
    ]
    quantidade = min(round(len(transactions) * UNCATEGORIZED_FRACTION), len(eligible))
    escolhidos = set(rng.sample(eligible, quantidade))
    return [
        replace(t, category_name=None) if index in escolhidos else t
        for index, t in enumerate(transactions)
    ]


def build_dataset(rng: random.Random | None = None) -> Dataset:
    """Monta os 12 meses de dados sinteticos. Determinista para um dado rng."""
    resolved = rng if rng is not None else random.Random(SEED_RANDOM_SEED)

    base = build_recurring() + build_variable(resolved)
    todas = base + build_transfers(base)
    todas.sort(key=_sort_key)
    todas = _drop_some_categories(todas, resolved)

    return Dataset(
        accounts=ACCOUNTS,
        categories=CATEGORIES,
        transactions=tuple(todas),
    )


def fingerprints(dataset: Dataset) -> set[str]:
    return {
        transaction_fingerprint(
            t.account_name, t.occurred_on, t.amount, t.description
        )
        for t in dataset.transactions
    }
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/seed/test_generator.py -v`
Expected: PASS, 13 testes

> Se `test_fingerprints_sao_unicos` falhar, duas transações coincidiram em conta,
> data, valor e descrição. Isso é colisão real de dados, não bug do hash: aumente
> a faixa de valores da regra culpada em `variable.py`. Não relaxe o teste — o
> `unique (fingerprint)` do banco rejeitaria a inserção na Task 11.

- [ ] **Step 5: Rodar a suíte completa**

Run: `python -m pytest -v`
Expected: PASS, todos

- [ ] **Step 6: Commit**

```bash
git add src/financeiro/seed/generator.py tests/seed/test_generator.py
git commit -m "feat: gerador puro do seed com fracao sem categoria"
```

---

### Task 11: Writer do seed, CLI e README da etapa 1

**Files:**
- Create: `src/financeiro/seed/writer.py`, `src/financeiro/seed/__main__.py`, `README.md`
- Test: `tests/seed/test_writer.py`

**Interfaces:**
- Consumes: `Dataset`, `build_dataset` (Task 10); `etl_connection` (Task 3); `transaction_fingerprint` (Task 1).
- Produces: `write_dataset(conn, dataset: Dataset) -> int` devolvendo o número de transações inseridas; `python -m financeiro.seed` como CLI.

- [ ] **Step 1: Escrever o teste, que falha**

Criar `tests/seed/test_writer.py`:

```python
from decimal import Decimal

import pytest

from financeiro.db.connection import app_connection, etl_connection
from financeiro.seed.generator import build_dataset
from financeiro.seed.writer import write_dataset

pytestmark = pytest.mark.db


def test_escreve_contas_categorias_e_transacoes(clean_db, settings):
    dataset = build_dataset()
    with etl_connection(settings) as conn:
        inseridas = write_dataset(conn, dataset)

    assert inseridas == len(dataset.transactions)

    with app_connection(settings) as conn:
        assert conn.execute("select count(*) from accounts").fetchone()[0] == 3
        assert conn.execute("select count(*) from categories").fetchone()[0] == len(
            dataset.categories
        )
        assert conn.execute("select count(*) from transactions").fetchone()[0] == len(
            dataset.transactions
        )


def test_e_idempotente_rodar_duas_vezes_nao_duplica(clean_db, settings):
    dataset = build_dataset()
    with etl_connection(settings) as conn:
        write_dataset(conn, dataset)
        write_dataset(conn, dataset)

    with app_connection(settings) as conn:
        assert conn.execute("select count(*) from transactions").fetchone()[0] == len(
            dataset.transactions
        )
        assert conn.execute("select count(*) from accounts").fetchone()[0] == 3


def test_valores_sobrevivem_ao_banco_como_numeric(clean_db, settings):
    dataset = build_dataset()
    with etl_connection(settings) as conn:
        write_dataset(conn, dataset)

    with app_connection(settings) as conn:
        total = conn.execute("select sum(amount) from transactions").fetchone()[0]
    esperado = sum((t.amount for t in dataset.transactions), Decimal("0"))
    assert total == esperado


def test_transacoes_sem_categoria_chegam_com_category_id_nulo(clean_db, settings):
    dataset = build_dataset()
    with etl_connection(settings) as conn:
        write_dataset(conn, dataset)

    esperado = sum(1 for t in dataset.transactions if t.category_name is None)
    with app_connection(settings) as conn:
        nulos = conn.execute(
            "select count(*) from transactions where category_id is null"
        ).fetchone()[0]
    assert nulos == esperado


def test_a_armadilha_da_contagem_dupla_existe_no_banco(clean_db, settings):
    """O mesmo teste da Task 9, agora em SQL: e isso que os evals vao medir."""
    with etl_connection(settings) as conn:
        write_dataset(conn, build_dataset())

    with app_connection(settings) as conn:
        soma_negativos = conn.execute(
            "select -sum(amount) from transactions where amount < 0"
        ).fetchone()[0]
        gasto_real = conn.execute(
            """
            select -sum(t.amount)
            from transactions t
            join categories c on c.id = t.category_id
            where t.amount < 0 and c.kind = 'expense'
            """
        ).fetchone()[0]

    assert soma_negativos > gasto_real
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/seed/test_writer.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'financeiro.seed.writer'`

- [ ] **Step 3: Implementar o writer**

Criar `src/financeiro/seed/writer.py`:

```python
"""Persistencia do Dataset. Unica parte do seed que precisa de banco.

Idempotente por truncate: o seed recria os dados do zero. O `fingerprint`
tambem protege contra duplicata, mas o truncate e o que torna
`python -m financeiro.seed` seguro de rodar duas vezes.
"""

import psycopg

from financeiro.domain.fingerprint import transaction_fingerprint
from financeiro.seed.generator import Dataset

_TRUNCATE = "truncate table transactions, categories, accounts restart identity cascade"


def write_dataset(conn: psycopg.Connection, dataset: Dataset) -> int:
    """Recria contas, categorias e transacoes. Devolve quantas transacoes inseriu."""
    with conn.cursor() as cur:
        cur.execute(_TRUNCATE)

        account_ids: dict[str, int] = {}
        for account in dataset.accounts:
            cur.execute(
                """
                insert into accounts (name, kind, institution)
                values (%s, %s, %s) returning id
                """,
                (account.name, str(account.kind), account.institution),
            )
            account_ids[account.name] = cur.fetchone()[0]

        category_ids: dict[str, int] = {}
        for category in dataset.categories:
            cur.execute(
                "insert into categories (name, kind) values (%s, %s) returning id",
                (category.name, str(category.kind)),
            )
            category_ids[category.name] = cur.fetchone()[0]

        rows = [
            (
                account_ids[t.account_name],
                category_ids[t.category_name] if t.category_name else None,
                t.occurred_on,
                t.amount,
                t.description,
                transaction_fingerprint(
                    t.account_name, t.occurred_on, t.amount, t.description
                ),
            )
            for t in dataset.transactions
        ]
        cur.executemany(
            """
            insert into transactions
                (account_id, category_id, occurred_on, amount, description, fingerprint)
            values (%s, %s, %s, %s, %s, %s)
            """,
            rows,
        )

    conn.commit()
    return len(dataset.transactions)
```

- [ ] **Step 4: Implementar a CLI**

Criar `src/financeiro/seed/__main__.py`:

```python
"""CLI do seed: python -m financeiro.seed"""

import sys

from financeiro.config import Settings, settings_from_env
from financeiro.db.connection import etl_connection
from financeiro.seed.generator import build_dataset
from financeiro.seed.writer import write_dataset


def main(settings: Settings | None = None) -> int:
    resolved = settings if settings is not None else settings_from_env()
    dataset = build_dataset()
    with etl_connection(resolved) as conn:
        inseridas = write_dataset(conn, dataset)
    print(
        f"seed aplicado: {len(dataset.accounts)} contas, "
        f"{len(dataset.categories)} categorias, {inseridas} transacoes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `python -m pytest tests/seed/test_writer.py -v`
Expected: PASS, 5 testes

- [ ] **Step 6: Rodar o seed de ponta a ponta**

```bash
docker compose up -d db
python -m financeiro.db.migrate
python -m financeiro.seed
```

Expected: `seed aplicado: 3 contas, 13 categorias, N transacoes`, com N entre 900 e 2200.

- [ ] **Step 7: Conferir à mão que os dados fazem sentido**

```bash
docker compose exec db psql -U postgres -d financeiro -c "select c.name, c.kind, count(*), -sum(t.amount) as total from transactions t left join categories c on c.id = t.category_id group by 1,2 order by 4 desc nulls last"
```

Expected: `alimentacao` entre as maiores despesas, `salario` com total negativo (é entrada), uma linha com `name` nulo (as não categorizadas), e `pagamento-fatura` presente.

- [ ] **Step 8: Escrever o `README.md`**

```markdown
# agente-financeiro

Agente de IA que responde perguntas em portugues sobre financas pessoais a
partir de transacoes bancarias, traduzindo a pergunta em SQL, executando, e
respondendo em linguagem natural com a consulta usada.

Projeto de portfolio. **Todos os dados sao sinteticos**, gerados por script.

- Design: [`docs/superpowers/specs/2026-11-16-agente-financeiro-design.md`](docs/superpowers/specs/2026-11-16-agente-financeiro-design.md)
- Plano da etapa 1: [`docs/superpowers/plans/2026-11-16-agente-financeiro-etapa-1.md`](docs/superpowers/plans/2026-11-16-agente-financeiro-etapa-1.md)

## Estado

| etapa | entrega | status |
|---|---|---|
| 1 | estrutura, banco, schema, seed sintetico | concluida |
| 2 | agente com `get_schema` e `run_sql` | pendente |
| 3 | categorizacao em lote | pendente |
| 4 | evals e comparacao | pendente |
| 5 | importador de OFX e CSV | pendente |
| 6 | README com resultados medidos | pendente |

## Como rodar

A etapa 1 **nao precisa de credencial da Anthropic**.

```bash
docker compose up -d db          # Postgres em localhost:5433
pip install -e ".[dev]"
python -m financeiro.db.migrate  # aplica as migrations
python -m financeiro.seed        # gera 12 meses de dados sinteticos
```

Testes:

```bash
# banco de teste: aplique as migrations nele uma vez
DATABASE_URL_OWNER=postgresql://postgres:postgres@localhost:5433/financeiro_test \
  python -m financeiro.db.migrate

python -m pytest                 # tudo
python -m pytest -m "not db"     # so o que nao precisa de Postgres
```

## Decisoes de design

As decisoes e o porque de cada uma estao na spec. As quatro que mais afetam
quem le o codigo:

**Dois papeis de banco.** `financeiro_app` so tem `SELECT` e e o papel do
agente; `financeiro_etl` escreve e e usado pelo seed, importador e
categorizacao. O agente roda numa conexao que fisicamente nao escreve — a
garantia e da permissao do Postgres, nao de validacao em Python.

**Gasto e `categories.kind = 'expense'`, nunca "amount negativo".** Uma compra
no cartao e o pagamento da fatura sao os dois negativos, mas somar os dois conta
o gasto duas vezes. Transferencia nao e gasto. Ha testes que garantem que essa
armadilha esta plantada nos dados, porque e isso que os evals vao medir.

**O gerador de dados e uma funcao pura.** `build_dataset()` devolve dataclasses
e nao toca no banco, entao determinismo, tendencia plantada e fracao sem
categoria sao testados em milissegundos, sem Postgres.

**Data de referencia fixa.** O seed gera de 2026-01-01 a 2026-12-31, nunca
relativo a `date.today()`. Com `today`, "quanto gastei em outubro?" teria uma
resposta em novembro e outra em janeiro, e o ground truth dos evals ficaria
errado sozinho.

## Avisos

`docker/initdb/` roda **apenas na criacao do volume**. Se alterar os papeis,
rode `docker compose down -v` antes de subir de novo, senao a mudanca nao tem
efeito.
```

- [ ] **Step 9: Rodar a suíte completa uma última vez**

Run: `python -m pytest -v`
Expected: PASS, todos os testes

- [ ] **Step 10: Confirmar que a suíte sem banco passa isolada**

Run: `python -m pytest -m "not db" -v`
Expected: PASS, e nenhum teste de banco coletado — prova que o gerador é puro.

- [ ] **Step 11: Commit**

```bash
git add src/financeiro/seed/writer.py src/financeiro/seed/__main__.py README.md tests/seed/test_writer.py
git commit -m "feat: writer do seed, cli e readme da etapa 1"
```

---

## Verificação final da etapa 1

- [ ] `docker compose down -v && docker compose up -d db` e, depois do healthcheck, as migrations e o seed rodam de ponta a ponta sem erro.
- [ ] `python -m pytest` passa inteiro.
- [ ] `python -m pytest -m "not db"` passa sem PostgreSQL no ar.
- [ ] `git status` está limpo e nenhum `.env` foi commitado.
- [ ] `git log --oneline` mostra um commit por task.
- [ ] Nenhum arquivo do projeto importa `anthropic`.
