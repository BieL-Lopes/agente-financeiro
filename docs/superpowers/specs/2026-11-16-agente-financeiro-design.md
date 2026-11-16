# Design — Agente de IA para perguntas sobre finanças pessoais

**Data:** 2026-11-16
**Status:** aprovado, pronto para plano de implementação

## 1. Objetivo

Um agente que responde perguntas em português sobre finanças pessoais a partir de
transações bancárias armazenadas em PostgreSQL. O usuário pergunta
"quanto gastei com mercado em outubro?" e o agente traduz a pergunta em SQL,
executa, e responde em linguagem natural mostrando a consulta usada.

Projeto de portfólio público. Dois critérios de sucesso, em ordem:

1. **Medição honesta.** O repositório mostra uma tabela de evals com taxa de
   acerto, SQL inválido, custo e latência, comparando duas arquiteturas e dois
   modelos. A metodologia é reproduzível por qualquer pessoa que clone o repo.
2. **Perímetro de segurança demonstrável.** `run_sql` aceita apenas leitura, e
   isso é garantido em duas camadas independentes, ambas testadas.

Não é objetivo: ser um app de finanças pessoais utilizável, substituir o app
React que o autor já mantém, nem suportar múltiplos usuários.

## 2. Escopo

### Dentro

- Schema relacional de três tabelas em PostgreSQL.
- Gerador de dados sintéticos determinístico (12 meses).
- Agente com duas tools: `get_schema` e `run_sql`.
- Duas arquiteturas de agente intercambiáveis: loop com tool use e pipeline fixo.
- Job offline de categorização de transações por LLM.
- Harness de evals com ground truth escrito à mão.
- Importador de OFX e CSV.
- API FastAPI com um endpoint de pergunta.
- Docker Compose, migrations, Pytest.

### Fora

- Autenticação e multi-tenancy.
- Frontend. (Opcional, só depois de tudo acima.)
- Conversa multi-turno. Decisão explícita: a API é **single-turn** — uma
  pergunta, uma resposta, sem histórico. Os evals são single-turn, então
  multi-turno seria estado não medido. Histórico entra depois, se entrar.
- Dados reais. Nenhum dado financeiro real entra no repositório, em nenhuma
  etapa, nem em fixture de teste.

## 3. Arquitetura

### 3.1 Módulos

```
agente-financeiro/
├── src/financeiro/
│   ├── db/            conexões, runner de migrations, migrations/*.sql
│   ├── domain/        modelos puros e regras de negócio
│   ├── seed/          gerador de dados sintéticos
│   ├── tools/         get_schema, run_sql  ← perímetro de segurança
│   ├── agent/         loop, pipeline, prompts
│   ├── categorize/    job offline de categorização
│   ├── importers/     OFX e CSV
│   └── api/           FastAPI
├── evals/             harness, dataset, queries de ground truth
├── tests/
├── docker/initdb/     criação dos papéis de banco
└── docker-compose.yml
```

A regra que define os limites: **cada módulo tem um propósito, uma interface
explícita, e é testável isoladamente.** A pergunta que guiou os cortes foi
"o que eu quero poder testar sem LLM e sem banco?".

**`domain/`** não importa nada do projeto. Dataclasses e funções puras, sem SQL,
sem HTTP, sem `anthropic`. Toda regra de negócio vive aqui, para não vazar para
dentro de string de SQL.

**`tools/`** é o perímetro de segurança e o módulo mais importante. Expõe
exatamente duas funções:

- `get_schema() -> str` — lê `information_schema` e devolve tabelas e colunas em
  texto. Lê do banco em vez de hardcodar: schema hardcodado desatualiza em
  silêncio e o agente passa a errar sem causa aparente.
- `run_sql(query: str) -> Rows` — valida com parser, executa, limita linhas.

Separado de `agent/` por três razões: a validação é testável sem LLM; é reusada
pelas duas arquiteturas; e concentra o perímetro em um arquivo que um revisor lê
inteiro.

**`agent/`** é o único módulo que fala com a API da Anthropic. Recebe as tools
por injeção de dependência, não as importa diretamente — assim os testes do loop
rodam com tools falsas, sem rede.

**`evals/`** fica fora de `src/` porque não é código de produto, e fora de
`tests/` porque é instrumento estatístico: custa dinheiro e roda sob demanda,
enquanto teste é binário e roda em CI.

### 3.2 Dois papéis de banco

| usuário | permissão | quem usa |
|---|---|---|
| `financeiro_app` | `SELECT` apenas | `tools/run_sql` → o agente |
| `financeiro_etl` | `SELECT`, `INSERT`, `UPDATE` | seed, importador, categorização |

O agente roda numa conexão que fisicamente não consegue escrever. A garantia é da
permissão do PostgreSQL; o parser de SQL é a primeira camada, não a que vale.

Criados por script em `docker/initdb/`, que o container do Postgres roda **apenas
na criação do volume**. Alterações exigem `docker compose down -v`; isso vai no
README.

## 4. Modelo de dados

```sql
create table accounts (
  id          bigserial primary key,
  name        text        not null,
  kind        text        not null check (kind in ('checking','savings','credit_card')),
  institution text        not null default '',
  created_at  timestamptz not null default now()
);

create table categories (
  id   bigserial primary key,
  name text not null unique,
  kind text not null check (kind in ('expense','income','transfer'))
);

create table transactions (
  id                  bigserial     primary key,
  account_id          bigint        not null references accounts(id),
  category_id         bigint        null references categories(id),
  occurred_on         date          not null,
  amount              numeric(12,2) not null,
  description         text          not null,
  fingerprint         text          not null unique,
  categorized_by      text          null check (categorized_by in ('llm','rule','manual')),
  category_confidence real          null,
  created_at          timestamptz   not null default now()
);

create index on transactions (occurred_on);
create index on transactions (account_id, occurred_on);
create index on transactions (category_id);
```

### 4.1 `numeric(12,2)`, não centavos inteiros

Decisão contrária ao conselho padrão, de propósito. A regra "dinheiro sempre em
centavos inteiros" existe porque aritmética de dinheiro normalmente acontece em
código de aplicação, onde float erra. Aqui a aritmética acontece dentro do
PostgreSQL, e `numeric` é decimal exato — o perigo que a regra evita não existe
neste contexto.

Em troca, centavos inteiros introduziriam um risco real: o LLM precisaria saber
que `amount` está em centavos e dividir por 100 em toda query. Quando esquecesse,
a resposta sairia errada por um fator de 100.

### 4.2 Valor com sinal e a armadilha da contagem dupla

`amount` é uma coluna com sinal: negativo é dinheiro saindo, positivo entrando.

O problema que isso cria, e que `categories.kind = 'transfer'` resolve: uma
compra de R$ 200 no cartão em outubro, mais o pagamento da fatura de R$ 200
debitado da conta corrente, somam R$ 400 em valores negativos — mas o gasto foi
de R$ 200. Pagamento de fatura e transferência para poupança não são gasto, são
dinheiro mudando de lugar.

Portanto: **gasto é `categories.kind = 'expense'`, nunca "amount negativo".** O
system prompt do agente afirma isso explicitamente, e o conjunto de evals inclui
perguntas desenhadas para pegar esse erro.

### 4.3 `category_id` nullable é a feature

`NULL` significa "ainda não categorizada". É o que o job de categorização procura
e o que o seed deixa de propósito em ~15% das linhas.

`categorized_by` e `category_confidence` existem para o README poder afirmar algo
medido sobre a categorização, em vez de adjetivo.

### 4.4 Deixado de fora de propósito

Sem tabela de usuários, sem soft delete, sem `updated_at` em `transactions`, sem
tags, sem orçamentos. Cada tabela a mais é mais schema para o LLM navegar.

Uma view desnormalizada (`v_transactions` com nome de categoria e conta já
unidos) reduziria joins e provavelmente aumentaria a acurácia. **Não entra agora**
porque não se sabe se é necessária: com três tabelas os joins são triviais. Se os
evals mostrarem erro de join, a view entra com justificativa medida, e essa
decisão documentada vale mais que a view.

## 5. Seed de dados sintéticos

A qualidade dos evals é limitada pela qualidade do seed. Dados uniformemente
aleatórios tornam perguntas como "qual categoria cresceu mais?" sem resposta
verdadeira — e tanto o agente quanto o ground truth "acertariam" ruído.

### 5.1 Propriedades obrigatórias

**Determinístico.** `random.Random(42)` explícito, nunca o módulo `random`
global. Rodar duas vezes produz exatamente as mesmas linhas. Sem isso, as queries
de ground truth escritas à mão apodrecem.

**Data de referência ancorada.** `SEED_REFERENCE_DATE = date(2026, 12, 31)` como
constante; o seed gera de 2026-01-01 a 2026-12-31. Nunca `date.today()`: com
`today`, "quanto gastei em outubro?" tem uma resposta em novembro e outra em
janeiro, e o ground truth fica errado sozinho.

**Descrições sujas.** `PAG*MERCADOLIVRE`, `UBER *TRIP`, `PIX ENVIADO JOAO M S`,
`SUPERM BOM PRECO LT`, `DROGASIL 1234` — abreviadas, truncadas em ~30 caracteres,
com prefixo de adquirente. Descrições limpas tornariam a categorização trivial e
a métrica da etapa de categorização sem significado.

### 5.2 Padrões plantados

Cada padrão existe para habilitar uma classe de pergunta nos evals:

| padrão | habilita |
|---|---|
| Salário fixo dia 5 | receita recorrente, sobra mensal |
| Aluguel fixo dia 10 | custo fixo |
| Mercado 4–6×/mês, com alta deliberada nos últimos 3 meses | "qual categoria cresceu mais?" |
| Um gasto anômalo de ~R$ 4.200 em saúde, em um único mês | outlier, "maior gasto do ano" |
| Pagamento de fatura mensal, `kind='transfer'` | armadilha da contagem dupla |
| Transferência mensal para poupança | "quanto consegui guardar?" |
| Um mês de férias: lazer e transporte ~3× acima | comparação entre períodos |
| ~15% das linhas com `category_id IS NULL` | job de categorização |

### 5.3 Volume e entrega

~1.500 transações em 12 meses, três contas (corrente, cartão, poupança). Grande
o bastante para agregações terem significado; pequeno o bastante para rodar em
segundos e para auditoria manual quando um eval discordar do agente.

`python -m financeiro.seed`, idempotente (trunca e recria), conectando como
`financeiro_etl`.

### 5.4 Testes do seed

Verificam propriedades, não valores:

- rodar duas vezes produz o mesmo conjunto de `fingerprint`;
- os 12 meses são contíguos;
- toda transação referencia conta existente;
- a fração sem categoria está entre 10% e 20%;
- cada pagamento de fatura casa com a soma das compras daquele cartão no mês
  anterior — garante que a armadilha da contagem dupla está realmente plantada.

## 6. O agente

### 6.1 Interface comum

Ambas as arquiteturas implementam:

```python
class Architecture(Protocol):
    def answer(self, question: str) -> Answer: ...
```

`Answer` carrega: texto da resposta, SQL executado, linhas retornadas, número de
chamadas de tool, tokens de entrada/saída/cache, latência. O harness de evals
troca uma arquitetura pela outra sem saber a diferença.

### 6.2 Arquitetura A — loop com tool use (padrão)

O modelo decide o que fazer: chama `get_schema`, monta uma query, chama
`run_sql`, examina o resultado, e responde ou tenta de novo. Implementado com o
Tool Runner do SDK Python (`client.beta.messages.tool_runner`, em beta), com
tools definidas via decorador `@beta_tool`, que gera o schema a partir da
assinatura da função.

Teto de iterações via `max_iterations` (6). O loop é o que permite recuperar de
um erro semântico: tentar `WHERE category = 'mercado'`, receber vazio, perceber
que categoria é `category_id` com join, e corrigir.

### 6.3 Arquitetura B — pipeline fixo (alternativa medida)

Schema injetado no system prompt. Uma chamada gera o SQL, o código executa, uma
segunda chamada transforma o resultado em resposta. Sem loop, sem decisão de
controle de fluxo pelo modelo. Mais barata, mais rápida, previsível, e sem
recuperação de erro.

B é um subconjunto do código de A sem o loop, então custa pouco mais que fazer
apenas A — e é a comparação mais interessante do README: "vale a pena o loop?".

### 6.4 Parâmetros da API da Anthropic

Preços e identificadores conforme a referência consultada em 2026-11-16 (tabela
do skill `claude-api`, cacheada em 2026-11-03). O harness lê a tabela de preços
de um módulo próprio, e o README registra a data da medição.

| modelo | id | entrada $/MTok | saída $/MTok | leitura de cache $/MTok |
|---|---|---|---|---|
| Claude Opus 5.5 | `claude-opus-5-5` | 4,00 | 20,00 | 0,20 |
| Claude Haiku 5.5 | `claude-haiku-5-5` | 0,10 | 0,50 | — |

Modelo padrão do produto: `claude-opus-5-5`. Segundo eixo dos evals:
`claude-haiku-5-5`, escolhido pelo contraste de custo (40× na entrada), que torna
a tabela do README informativa.

Decisões concretas de chamada, todas verificadas na referência:

- **`tool_choice` fica em `auto`** (o padrão). Forçar chamada
  (`{"type": "any"}` ou `{"type": "tool", ...}`) retorna **400** em
  Claude Opus 5.5. Para manter a garantia de schema, cada tool leva
  `strict: True` com `additionalProperties: false` no `input_schema`. Como `auto`
  não garante chamada, o código verifica se veio bloco `tool_use` e re-pergunta
  se não veio.
- **Thinking não é configurado.** Em Claude Opus 5.5 o thinking é sempre ativo;
  `{"type": "disabled"}` e `budget_tokens` retornam 400. O parâmetro `thinking` é
  omitido e a profundidade é controlada por `output_config={"effort": ...}`, cujo
  padrão neste modelo é `medium`. O effort fica explícito no código, fixado em
  `medium` em todas as células dos evals. Varrer o effort é um terceiro eixo
  **fora do escopo da primeira rodada** — entra só depois que a comparação
  arquitetura × modelo estiver publicada, para não multiplicar as células antes
  do tempo.
- **`max_tokens = 16000`** nas chamadas não-streaming.
- **Sem prefill de mensagem de assistente** — retorna 400 nestes modelos.
- **`fallbacks` de recusa ativado por padrão** no caminho interativo:
  `betas=["server-side-fallback-2026-07-01"]` com `fallbacks="default"`. Não é
  aceito na Batches API (ver 7.3).
- **Parsear `tool_use.input` com `json.loads`**, nunca casar string no JSON
  serializado: o escaping pode variar.

### 6.5 Cache de prompt

A ordem de renderização é `tools` → `system` → `messages`, e o cache é por
prefixo: qualquer byte alterado no prefixo invalida tudo depois dele.

Portanto: schema e instruções no `system`, com
`cache_control={"type": "ephemeral"}` no último bloco estável; a pergunta do
usuário vai em `messages`, depois do breakpoint. A lista de tools precisa ser
determinística — ordem fixa, nunca derivada de `set` ou `dict` não ordenado,
porque ela é renderizada antes do `system` e uma reordenação invalida o cache
inteiro.

Verificação obrigatória: `usage.cache_read_input_tokens` maior que zero em
requisições repetidas. Se vier zero, há um invalidador silencioso (timestamp no
prompt, JSON com chaves fora de ordem, tool set variável). O harness de evals
registra esse campo por execução, e um valor zero inesperado falha a rodada.

## 7. Evals

### 7.1 Conjunto de perguntas

Cada item do dataset tem: pergunta em português, query SQL de ground truth
escrita à mão, e a classe da pergunta.

**Começa com 12 perguntas e cresce até 30.** Doze cobrem as classes abaixo; o
custo de escrever e auditar ground truth à mão é o fator limitante, e um conjunto
pequeno e correto vale mais que um grande e duvidoso.

Classes obrigatórias:

1. agregação simples ("quanto gastei com mercado em outubro?");
2. filtro temporal relativo ("nos últimos 3 meses");
3. comparação entre períodos ("qual categoria cresceu mais?");
4. ranking ("meus 5 maiores gastos do ano");
5. **armadilha da contagem dupla** ("quanto gastei em outubro?", com ground truth
   excluindo `kind='transfer'`);
6. **pergunta sem resposta nos dados** ("quanto vou gastar em janeiro?",
   "qual meu score de crédito?") — o comportamento correto é dizer que não sabe.

A classe 6 é obrigatória: sem ela os evals medem só o caminho felizmente, e o
comportamento mais difícil de acertar num agente text-to-SQL é recusar.

### 7.2 Métricas

Medidas por execução e agregadas por célula (arquitetura × modelo):

As perguntas respondíveis (classes 1 a 5) e as sem resposta (classe 6) são
pontuadas por critérios diferentes e **relatadas separadamente**: uma pergunta da
classe 6 nunca conta como acerto ou erro numérico, e uma das classes 1 a 5 nunca
conta como recusa correta. A taxa de acerto do README é sobre as classes 1 a 5,
com a contagem de recusas reportada ao lado.

| métrica | aplica-se a | definição |
|---|---|---|
| acerto | classes 1–5 | resultado numérico igual ao do ground truth, com tolerância de R$ 0,01 |
| SQL inválido | todas | a query levantou erro de sintaxe, coluna ou permissão |
| SQL válido com resposta errada | classes 1–5 | executou, mas o número divergiu |
| recusa correta | classe 6 | disse que não sabe |
| recusa incorreta | classes 1–5 | disse que não sabe numa pergunta respondível |
| custo | todas | dólares, calculado de `usage` e da tabela de preços |
| latência | todas | segundos de ponta a ponta, p50 e p90 |
| chamadas de tool | todas | número de iterações até a resposta |

"SQL inválido" e "SQL válido com resposta errada" são métricas **separadas**:
a primeira é problema de prompt ou de schema, a segunda é de raciocínio. Juntá-las
esconde qual precisa ser corrigida.

### 7.3 Execução e custo

Dimensionamento: 2 arquiteturas × 2 modelos × 12 perguntas = 48 execuções por
rodada, crescendo para 120 quando o dataset chegar a 30.

A arquitetura B (pipeline) pode rodar pela **Batches API**, a 50% do custo, por
ser de chamada única. A arquitetura A (loop) **não pode**: cada turno depende do
resultado da tool do turno anterior, e batch é assíncrono e sem round-trip. Essa
assimetria é real e vai documentada — faz parte do custo de operar um loop.
Observação: `fallbacks` é rejeitado na Batches API, e forçar `tool_choice`
também, então o braço em batch roda sem esses parâmetros.

Antes de cada rodada, `client.messages.count_tokens` estima o custo de entrada e
o harness imprime a estimativa, exigindo confirmação acima de um limite
configurado. Nenhuma rodada de eval dispara sem o custo estimado na tela.

## 8. Categorização

Job offline, **não tool do agente**. Decisão com razão específica: como tool, a
categorização produziria escritas não-determinísticas no meio de um fluxo
projetado como somente-leitura, quebraria a garantia do usuário read-only, e
tornaria os evals não-determinísticos (a mesma pergunta daria respostas
diferentes antes e depois da categorização).

Forma: comando `python -m financeiro.categorize`, conectando como
`financeiro_etl`. Seleciona `WHERE category_id IS NULL`, envia em lotes ao LLM
com structured outputs (`output_config.format` com `json_schema`), e grava
`category_id`, `categorized_by='llm'` e `category_confidence`. Linhas abaixo de
um limiar de confiança ficam marcadas para revisão em vez de categorizadas.

## 9. Infraestrutura

`docker-compose.yml` com dois serviços:

- **`db`**: `postgres:17-alpine`, porta **5433** no host (evita colisão com
  Postgres local), volume nomeado, `docker/initdb/` montado, e `healthcheck` com
  `pg_isready`.
- **`api`**: build local, `depends_on: {db: {condition: service_healthy}}`.
  `depends_on` sozinho só espera o container subir, não o Postgres aceitar
  conexão.

`ANTHROPIC_API_KEY` vem do ambiente com default vazio. **Propriedade de design:
a etapa 1 funciona sem credencial nenhuma** — `docker compose up`, seed e
consulta ao banco rodam sem conta na Anthropic. Só o agente exige a chave.

### 9.1 Migrations em SQL puro

Arquivos numerados em `src/financeiro/db/migrations/NNN_*.sql`, aplicados por um
runner que registra versões aplicadas em `schema_migrations`.

Alembic foi considerado e dispensado: seu valor (autogenerate, branching,
downgrade) se paga em schema grande com times paralelos. Com três tabelas, ele
adiciona indireção entre o revisor e o schema. Escolher a ferramenta do tamanho
do problema e justificar é mais defensável que usar a ferramenta famosa.

**Toda migration vai para o git, sempre.** Regra em primeiro lugar porque o autor
perdeu um schema de sete tabelas num projeto anterior por tê-la violado: a
migration existia em uma máquina e sumiu com ela.

## 10. Testes

| camada | depende de | cobre | velocidade |
|---|---|---|---|
| unitário | nada | `domain/`, validação de SQL, gerador de seed | ms |
| integração | PostgreSQL | migrations, `run_sql` real, seed completo | segundos |
| agente | cliente Anthropic falso | loop, `max_iterations`, erro de tool | ms |

A camada do agente usa um cliente falso com respostas roteirizadas. Testa se o
agente chamou `get_schema` antes de tentar SQL, se respeitou o teto de iterações,
e se tentou corrigir quando `run_sql` levantou exceção — sem gastar token nem
depender de rede. Loop testado apenas com LLM real é loop não testado.

Banco de teste: `financeiro_test` no mesmo PostgreSQL do compose, com truncate
entre testes. `testcontainers` foi dispensado: adiciona dependência e é lento no
Windows.

### 10.1 Suíte de segurança do `run_sql`

| entrada | esperado |
|---|---|
| `DELETE FROM transactions` | recusado pelo parser |
| `SELECT 1; DROP TABLE accounts` | recusado (múltiplos statements) |
| `WITH x AS (DELETE FROM transactions RETURNING *) SELECT * FROM x` | recusado |
| `SELECT pg_sleep(60)` | timeout |
| `SELECT * FROM transactions` (1.500 linhas) | truncado em 50 |
| `COPY ... TO PROGRAM ...` | recusado |
| `INSERT` executado direto como `financeiro_app` | `permission denied` |

A terceira linha é a razão de usar um parser (`sqlglot`) e não regex: uma
validação por prefixo (`startswith("select")`) **aprova** aquele CTE, que apaga a
tabela. O parser examina a árvore e encontra o `DELETE`.

A última linha testa a segunda camada: mesmo com o parser falhando, o PostgreSQL
recusa. Defesa em profundidade testada nas duas camadas.

Timeout por query: `statement_timeout` na conexão, 5 segundos. Limite de linhas:
50, aplicado no cursor, não em `LIMIT` dentro do SQL do modelo — que o modelo
poderia omitir.

## 11. Ordem de entrega

Invertida em relação ao brief original: o importador de OFX/CSV sai de segundo
para quinto. Razão: o seed da etapa 1 já destrava as etapas 3 a 5, e OFX é um
formato hostil (SGML pré-XML, encoding inconsistente) cujo trabalho não produz
nada demonstrável. Construir o agente antes torna o repositório apresentável mais
cedo.

| etapa | entrega |
|---|---|
| 1 | estrutura, docker-compose, migrations, schema, seed, testes do seed |
| 2 | agente com `get_schema` e `run_sql`, arquiteturas A e B, suíte de segurança |
| 3 | job de categorização em lote |
| 4 | harness de evals, dataset de 12 perguntas, tabela de comparação |
| 5 | importador de OFX e CSV, com testes |
| 6 | README com diagrama, decisões e resultados medidos |

Cada etapa tem seu próprio plano de implementação. Esta spec cobre o desenho de
todas; a etapa 1 é a primeira a virar plano.

## 12. Riscos

**O conjunto de evals pode ser pequeno demais para distinguir as arquiteturas.**
Com 12 perguntas, uma diferença de uma resposta é ~8 pontos percentuais, dentro
do ruído. Mitigação: reportar o número absoluto de acertos junto da porcentagem,
nunca só a porcentagem, e crescer o dataset antes de afirmar que uma arquitetura
vence a outra.

**O Tool Runner está em beta.** A interface pode mudar. Mitigação: `agent/` o
isola atrás de `Architecture`, e o loop manual está documentado na referência
como alternativa se o beta atrapalhar.

**Os preços da API mudam.** A tabela da seção 6.4 é de 2026-11-16. Mitigação:
preços num módulo único, e o README registra a data da medição junto dos
resultados.

**Dados sintéticos podem ser fáceis demais.** Se o agente acertar 100% na
primeira rodada, o conjunto não discrimina. Mitigação: nesse caso, adicionar
perguntas mais difíceis em vez de celebrar — um eval que todo mundo passa não
mede nada.
