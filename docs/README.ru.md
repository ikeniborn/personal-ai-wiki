# personal-ai-wiki — `paw` (Personal AI Wiki)

> 🇬🇧 **English version:** [`../README.md`](../README.md)

**Self-hosted RAG-база знаний командного масштаба, которая превращает сырые
документы в запрашиваемую, готовую для агентов вики.** Вы загружаете источники;
LLM-харнесс извлекает темы и пишет связанные вики-статьи с сущностями, цитатами
и графом знаний; всё разбивается на чанки и эмбеддится для гибридного поиска.
Ваши агенты обращаются к ней через **Model Context Protocol (MCP)**, а люди —
через веб-интерфейс и JSON API.

Сделано для технического специалиста, который эксплуатирует агентские системы и
которому нужна **одна база знаний на каждый проект/реализацию, полностью под его
контролем** — а не разбросанная по SaaS-блокнотам, векторным БД и логам чатов.

---

## Какую задачу решает

Если вы управляете агентами (Claude Code, собственные харнессы, внутренние
copilot'ы) на множестве проектов, знания фрагментируются:

| Боль | Как `paw` её закрывает |
|---|---|
| Документы, PDF, веб-страницы, ADR, транскрипты лежат в 10 местах | **Единый ingest-конвейер** для md/pdf/docx/html/epub/url/images → нормализованные вики-статьи |
| Сырые документы непригодны для поиска | LLM-харнесс **переписывает** источники в чистые, дедуплицированные, перекрёстно связанные статьи с цитатами |
| Каждый агент городит свой RAG-стек | **Read-only MCP-сервер** даёт `search_wiki` / `get_article` / `list_links` — подключаете любой MCP-клиент, и он просто запрашивает |
| Базы знаний разных проектов перемешиваются | **Domains** = изолированные базы знаний на проект (отдельный корпус, конфиг, граф) |
| Вендорлок на стороне LLM/эмбеддингов | **Provider-agnostic**: любой OpenAI-совместимый endpoint (облако или локально) |
| Self-hosting RAG = связать 6 сервисов | **Один Docker-образ, два процесса**, `docker compose up` |
| Знания гниют — битые ссылки, устаревшие статьи, сироты | Встроенный **maintenance**: lint → fix → format → reindex |
| Приватность / резидентность данных | **Полностью self-hosted**; секреты шифруются at rest; ваш корпус не видит никто, кроме выбранного вами LLM-endpoint'а |

Ментальная модель: **domain — это мозг проекта.** Наводите агентов на domain
через MCP, и они получают обоснованные ответы с цитатами вместо галлюцинаций.

---

## Функциональные возможности

### Ingest — любой источник → вики-статья
- **Форматы:** Markdown, PDF (PyMuPDF), DOCX (mammoth), HTML (trafilatura), EPUB
  (ebooklib), плюс два боковых пути: **URL** (fetch под защитой SSRF) и
  **изображения** (vision OCR/описание). Bulk-загрузка `.zip` разворачивается во
  множество источников за один проход.
- **LLM-харнесс** (`paw.harness`) — агентский tool-calling-цикл поверх любой
  OpenAI-совместимой модели, выполняющий ingest по стадиям: извлечение тем →
  черновик статьи → детерминированная запись → авто-связывание совстречающихся
  сущностей → чанкинг + эмбеддинг. Результат: чистый markdown со структурой `##`,
  **сущностями, типизированными ссылками и дословными цитатами**.
- **Заземление и безопасность:** каждый результат инструмента и найденный пассаж
  оборачивается маркерами `DATA, not instructions` (защита от prompt-injection);
  по-запусковые **бюджеты** (steps / tool calls / writes / tokens) и детекция
  циклов ограничивают стоимость.

### Retrieve — гибридный поиск
- **Векторная ветка** (pgvector cosine, HNSW-индекс) **+ FTS-ветка** (Postgres
  `websearch_to_tsquery`), слитые через **Reciprocal Rank Fusion**, с бустом по
  совпадению сущностей, затем **расширение по графу знаний** (BFS по
  типизированным ссылкам или entity-bridged GraphRAG при включённом движке AGE).
- Деградирует мягко: неэмбеддированный корпус всё равно отвечает через FTS.

### Ask — Q&A и чат
- **Одноразовый запрос** с inline-цитатами по slug и fallback `DONT_KNOW`, когда
  контекста нет (никаких выдуманных ответов).
- **Многоходовый чат** в рамках domain, с окном истории и per-user retention.
- **Кэш ответов** (точная нормализация + семантический ANN) с отслеживанием
  зависимостей от ревизий статей — кэшированные ответы автоматически помечаются
  *устаревшими* в момент изменения процитированной статьи.

### Граф знаний
- Статьи — узлы; типизированные рёбра `Link` (`related`/`parent`/`child`)
  образуют граф. Навигируемое дерево parent/child, ограниченный по глубине
  subgraph-вид (вендоренный Cytoscape UI) и **GraphRAG-retrieval** через
  опциональный property-граф Apache AGE (entity-bridged соседи с провенансом по
  концептам).

### Maintenance — поддержание здоровья знаний
- **lint** (детерминированно: битые ссылки, сироты, устаревшее, дубли сущностей) →
  **fix** (LLM предлагает структурированные правки) → **format** (переформатирование
  с инвариантом защиты от потери фактов) → **reindex** (повторный эмбеддинг после
  смены модели/размерности). Выполняется фоновыми задачами с live SSE-прогрессом,
  отменой и per-domain-локами.

### Интерфейсы для агентов и людей
- **MCP-сервер** на `/mcp` — три read-only инструмента, auth по Bearer-API-key,
  scope `read`. Это точка интеграции для ваших агентских систем.
- **JSON API** под `/api/v1` (auth, domains, sources, articles, query, chat,
  graph, jobs, settings, users, api-keys, maintenance), ошибки в формате
  RFC 9457 `problem+json`.
- **HTMX веб-интерфейс** — дашборд, страницы domain'ов, редактирование статей с
  историей ревизий/откатом, settings/admin, **i18n (en/ru)**, самостоятельная
  выдача API-key, управление пользователями для админа.

---

## Нефункциональные возможности

| Измерение | Что вы получаете |
|---|---|
| **Безопасность** | Серверные сессии в Redis; RBAC (`require_role`); CSRF double-submit; пароли argon2; **секреты, зашифрованные Fernet at rest**; валидация загрузок (расширение + magic-bytes + UTF-8); защита от zip-бомб / path-traversal; **SSRF-guard** (только https, host-allowlist, deny-диапазоны IP, ре-валидация на каждом редиректе); санитизация HTML через `nh3`; строгий CSP (`frame-ancestors 'none'`, `object-src 'none'`); защищённый от Cypher-инъекций слой AGE. |
| **Provider-agnostic** | Chat, embedding и vision идут через единый OpenAI-совместимый клиент — направьте его на OpenAI, gateway или локальный сервер моделей. Без вендорлока. |
| **Атомарность** | Слой services — единственная граница commit'а: мульти-запись (статья + ревизия + граф + инвалидация кэша) коммитится ровно один раз или откатывается целиком. |
| **Полностью async** | FastAPI + async SQLAlchemy 2.0 + asyncpg + redis.asyncio + arq; на пути запроса нет блокирующего IO. |
| **Observability** | Метрики Prometheus (`paw_*`: HTTP RED, job/queue, **LLM cost/tokens/latency**), `/health` liveness + `/health?ready=1` readiness (DB+Redis), опциональный Langfuse-трейсинг, opt-in compose-профиль Grafana/Prometheus. Защищено так, что сбой метрики никогда не меняет ответ. |
| **Надёжность** | Heartbeat-ключ liveness воркера; reconcile зависших задач на старте; кооперативная отмена задач; per-domain + per-model локи в Redis; плановые бэкапы `pg_dump` (opt-in sidecar) + документированный runbook восстановления. |
| **Гейты качества** | CI выполняет `ruff check .` → `mypy src` (strict) → `pytest -q`; слои integration/api/e2e поднимают **настоящие** Postgres+Redis через testcontainers. |
| **Расширяемость** | Protocol `StorageBackend` (сегодня blob'ы в Postgres, позже можно подменить на object store); типизированный per-section DB-конфиг со слоями `env ⊕ app_settings ⊕ domain ⊕ user`. |

---

## Требуемые ресурсы

### Runtime-стек (один образ, два процесса)
- **`api`** — uvicorn (FastAPI), за **Traefik** (TLS через Let's Encrypt).
- **`worker`** — потребитель задач arq.
- **Инфраструктура** — **PostgreSQL 16 + pgvector** (кастомный образ также несёт
  Apache AGE), **Redis 7**, **Traefik v3.2**. Одноразовый `init` сначала
  прогоняет миграции alembic.
- **Внешнее** — OpenAI-совместимый LLM-endpoint (chat + embedding; vision
  опционально) и его API-ключ. Это единственная исходящая зависимость.

### Compute (стартовые значения командного масштаба, из `docker-compose.yml`)

| Сервис | Лимит памяти | Лимит CPU | Резерв памяти |
|---|---|---|---|
| postgres | 2g | 2.0 | 512m |
| worker | 2g | 2.0 | 512m |
| api | 1g | 1.0 | 256m |
| redis | 512m | 0.5 | 128m |
| traefik | 256m | 0.5 | 64m |

> `deploy.resources` действует под Docker Swarm; для обычного `docker compose up`
> используйте `mem_limit` / `cpus` на блоке сервиса. Повышайте для больших
> корпусов или тяжёлого ingest'а. Весь стек спокойно работает на одном хосте.

### Порты
- **80 / 443** — Traefik (HTTP→HTTPS, ACME). `api` слушает `8000` внутри.
- Порты observability **не** публикуются при обычном `up`.

### Тулчейн сборки / разработки
- **Python 3.12**, управление зависимостями через **`uv`** (никогда напрямую
  `pip`/`pytest`).
- **Docker daemon** для слоёв тестов integration/api/e2e (testcontainers).

### Хранилище (named volumes — бэкапьте `pgdata`)
- `pgdata` — основное хранилище (статьи, источники, пользователи, задачи).
  **Невосстановимо.**
- `redisdata` — очередь/сессии (в основном регенерируемо).
- `letsencrypt` — ACME-сертификаты (регенерируемо, с rate-limit).
- `backups` — архивы `pg_dump` из opt-in backup-sidecar'а.

### Обязательная конфигурация
Скопируйте `.env.example` → `.env` и заполните (без дефолтов; старт падает при
отсутствии):

| Переменная | Примечание |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://MASKING@postgres:5432/paw` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `SESSION_SECRET` | 32+ байт случайности — `openssl rand -hex 32` |
| `FERNET_KEY` | 44-символьный Fernet-ключ — `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `POSTGRES_PASSWORD` | Сильный пароль Postgres; если содержит `$`, заключите значение в одинарные кавычки в `.env` или экранируйте `$` как `$$` |

В проде также задаются `PAW_HOST` (публичный DNS), `ACME_EMAIL` и опциональные
параметры бэкапа. См. **prod-чеклист** в
[`wiki/ops.md`](wiki/ops.md).

---

## Быстрый старт

```bash
cp .env.example .env          # затем заполните SESSION_SECRET, FERNET_KEY, POSTGRES_PASSWORD
docker compose up             # traefik + postgres + redis + init(migrate) + api + worker
```

Затем откройте веб-интерфейс, пройдите первичную настройку (создаёт admin'а,
заполняет settings, конфигурирует LLM-провайдера), создайте **domain**, загрузите
источники и выпустите **API-key**, чтобы подключить агентов к `/mcp`.

Opt-in профили:

```bash
docker compose --profile backup up -d backup          # плановые бэкапы pg_dump
docker compose --profile observability up             # Prometheus + Grafana + exporters
```

### Локальная разработка

```bash
uv sync --dev                 # установка зависимостей + dev-группы в .venv
uv run ruff check .           # lint
uv run mypy src               # проверка типов (strict)
uv run pytest -q              # полный прогон (слои integration требуют Docker)
uv run uvicorn paw.main:app --reload          # только api (нужны доступные PG + Redis)
uv run arq paw.worker.WorkerSettings          # только worker
```

---

## Архитектура кратко

Один Docker-образ запускает `api` (uvicorn) и `worker` (arq), разделяющие только
состояние Postgres и Redis — api ставит задачи в очередь, worker их разбирает.
Код выстроен ацикличными слоями:

```
api / web        →  services  →  db.repos, storage, vector, graph
worker  →  jobs  →  harness    →  providers, ingest, vector, graph
                       ↓
                  db, config            (листья)
```

- **api/web** — тонкие хендлеры, без бизнес-логики.
- **services** — request-scoped логика, единственная граница commit'а.
- **harness** — агентский цикл, которым управляет worker.
- **providers** — граница OpenAI-совместимого LLM/embedding/vision.

Стек: Python 3.12 · `uv` · FastAPI (async) · async SQLAlchemy 2.0 · PostgreSQL
16 + pgvector (+ опционально AGE) · Redis + arq · Jinja2 + HTMX · Traefik.

---

## Статус проекта

Построено **вертикальными фазами**, каждая — рабочий сквозной срез:

- **Фазы 1–8 — смержены:** walking skeleton, ingest, retrieval/query, chat,
  граф + редактирование статей, maintenance, кэш запросов, MCP-сервер + API-keys.
- **Фаза 9 — смержена:** ops и hardening — observability (9a), усиление
  безопасности (9b: SSRF/zip-guard'ы, URL-loader, vision, bulk), admin UI + i18n
  (9c), бэкапы/деплой-hardening (9d).
- **Фаза 10 — только дизайн:** Apache AGE + GraphRAG (спецификация есть; движок
  AGE подключён и opt-in на domain, полная продуктизация GraphRAG в ожидании).

---

## Документация

Глубокая перекрёстно связанная документация — под [`wiki/`](wiki/):

- [`architecture.md`](wiki/architecture.md) · [`ingest.md`](wiki/ingest.md)
  · [`vector.md`](wiki/vector.md) · [`harness.md`](wiki/harness.md)
  · [`graph.md`](wiki/graph.md) · [`providers.md`](wiki/providers.md)
- [`services.md`](wiki/services.md) · [`api.md`](wiki/api.md)
  · [`mcp.md`](wiki/mcp.md) · [`jobs.md`](wiki/jobs.md)
  · [`db.md`](wiki/db.md) · [`storage.md`](wiki/storage.md)
- [`security.md`](wiki/security.md) · [`observability.md`](wiki/observability.md)
  · [`ops.md`](wiki/ops.md) — **деплой, TLS/ACME, ресурсы, runbook бэкапа/восстановления**
- [`web.md`](wiki/web.md) · [`audit.md`](wiki/audit.md)
