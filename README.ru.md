### ⚠️ Объявление: Смена приоритетов в пользу AsyncReview
---

**ВАЖНОЕ ОБНОВЛЕНИЕ**: Поддержка DeepWiki-Open продолжается, но основная активная разработка переносится в **[AsyncReview](https://github.com/AsyncFuncAI/AsyncReview/)**. Спасибо за поддержку этого проекта — присоединяйтесь к новому репозиторию, который станет главным направлением работы в этом году.

---
---

# DeepWiki-Open

![Баннер DeepWiki](screenshots/Deepwiki.png)

**DeepWiki** — это моя собственная реализация DeepWiki, автоматически создающая красивые, интерактивные вики по любому репозиторию на GitHub, GitLab или BitBucket! Просто укажите название репозитория, и DeepWiki выполнит:

1. Анализ структуры кода
2. Генерацию полноценной документации
3. Построение визуальных диаграмм, объясняющих работу системы
4. Организацию всего в удобную и структурированную вики

[!["Купить мне кофе"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/sheing)  
[![Поддержать в криптовалюте](https://tip.md/badge.svg)](https://tip.md/sng-asyncfunc)  
[![Twitter/X](https://img.shields.io/badge/Twitter-1DA1F2?style=for-the-badge&logo=twitter&logoColor=white)](https://x.com/sashimikun_void)  
[![Discord](https://img.shields.io/badge/Discord-7289DA?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com/invite/VQMBGR8u5v)

[English](./README.md) | [简体中文](./README.zh.md) | [繁體中文](./README.zh-tw.md) | [日本語](./README.ja.md) | [Español](./README.es.md) | [한국어](./README.kr.md) | [Tiếng Việt](./README.vi.md) | [Português Brasileiro](./README.pt-br.md) | [Français](./README.fr.md) | [Русский](./README.ru.md)

## ✨ Возможности

- **Мгновенная документация**: Превращение любого репозитория в вики за считанные секунды
- **Поддержка приватных репозиториев**: Безопасный доступ с помощью персональных токенов
- **Умный анализ**: Понимание структуры и взаимосвязей в коде с помощью ИИ
- **Красивые диаграммы**: Автоматическая генерация диаграмм Mermaid для отображения архитектуры и потоков данных
- **Простая навигация**: Интуитивный интерфейс для изучения вики
- **Функция “Спросить”**: Общение с репозиторием через ИИ, основанный на RAG, для получения точных ответов
- **DeepResearch**: Многошаговое исследование для глубокого анализа сложных тем
- **Поддержка различных провайдеров моделей**: Google Gemini, OpenAI, OpenRouter и локальные модели Ollama
- **Гибкие эмбеддинги**: Выбор между OpenAI, Google AI или локальными эмбеддингами Ollama для оптимальной производительности

### Корпоративные функции

- **GitLab SSO**: Единая аутентификация на основе OAuth2 через GitLab
- **Панель администратора**: Пакетная индексация, управление проектами, мониторинг системы
- **MCP-сервер**: JWT-аутентифицированная точка доступа [Model Context Protocol](https://modelcontextprotocol.io/) — подключайте Claude Code, Codex или любой MCP-клиент для запросов к вашей кодовой базе
- **Управление продуктами**: Объединение нескольких репозиториев в логические продукты для кросс-репозиторного анализа
- **Связи репозиториев**: Автоматическая визуализация графа зависимостей между репозиториями
- **Глобальный Ask**: Кросс-репозиторные вопросы и ответы по всем проиндексированным проектам
- **Структурированные инсайты**: LLM-извлечение модулей, API-эндпоинтов, моделей данных и технологического стека по каждому проекту
- **Система прав доступа**: Контроль доступа на основе GitLab с кэшированием в памяти (по проекту — 5 мин, список проектов — 24 ч)

## 🚀 Быстрый старт (максимально просто!)

### Вариант 1: С использованием Docker

```bash
# Клонируйте репозиторий
git clone https://github.com/AsyncFuncAI/deepwiki-open.git
cd deepwiki-open

# Создайте файл .env с вашими API-ключами
echo "GOOGLE_API_KEY=ваш_google_api_key" > .env
echo "OPENAI_API_KEY=ваш_openai_api_key" >> .env
# Необязательно: ключ OpenRouter
echo "OPENROUTER_API_KEY=ваш_openrouter_api_key" >> .env
# Необязательно: указать хост Ollama, если он не локальный (по умолчанию http://localhost:11434)
echo "OLLAMA_HOST=ваш_ollama_host" >> .env
# Необязательно: ключ и параметры Azure OpenAI
echo "AZURE_OPENAI_API_KEY=ваш_azure_api_key" >> .env
echo "AZURE_OPENAI_ENDPOINT=ваш_azure_endpoint" >> .env
echo "AZURE_OPENAI_VERSION=ваша_azure_version" >> .env
# Запуск через Docker Compose
docker-compose up
```

Подробную инструкцию по работе с Ollama и Docker см. в [Ollama Instructions](Ollama-instruction.md).

> 💡 **Где взять ключи API:**
> - [Google AI Studio](https://makersuite.google.com/app/apikey)
> - [OpenAI Platform](https://platform.openai.com/api-keys)
> - [Azure Portal](https://portal.azure.com/)

### Вариант 2: Ручная установка (рекомендуется)

#### Шаг 1: Установка ключей API

Создайте файл `.env` в корне проекта со следующим содержанием:

```
GOOGLE_API_KEY=ваш_google_api_key
OPENAI_API_KEY=ваш_openai_api_key
# Необязательно: для OpenRouter
OPENROUTER_API_KEY=ваш_openrouter_api_key
# Необязательно: для Azure OpenAI
AZURE_OPENAI_API_KEY=ваш_azure_api_key
AZURE_OPENAI_ENDPOINT=ваш_azure_endpoint
AZURE_OPENAI_VERSION=ваша_azure_version
# Необязательно: если Ollama не локальная
OLLAMA_HOST=ваш_ollama_host
```

#### Шаг 2: Запуск backend-сервера

```bash
# Установка зависимостей
python -m pip install poetry==2.0.1 && poetry install -C api

# Запуск API
python -m api.main
```

#### Шаг 3: Запуск frontend-интерфейса

```bash
# Установка JS-зависимостей
npm install
# или
yarn install

# Запуск веб-интерфейса
npm run dev
# или
yarn dev
```

#### Шаг 4: Используйте DeepWiki!

1. Откройте [http://localhost:3000](http://localhost:3000) в браузере
2. Введите URL репозитория (например, `https://github.com/openai/codex`)
3. Для приватных репозиториев нажмите “+ Add access tokens” и введите токен
4. Нажмите “Generate Wiki” и наблюдайте за магией!

## 🔍 Как это работает

DeepWiki использует искусственный интеллект, чтобы:

1. Клонировать и проанализировать репозиторий GitHub, GitLab или Bitbucket (включая приватные — с использованием токенов)
2. Создать эмбеддинги кода для интеллектуального поиска
3. Сгенерировать документацию с учетом контекста (с помощью Google Gemini, OpenAI, OpenRouter, Azure OpenAI или локальных моделей Ollama)
4. Построить визуальные диаграммы для отображения связей в коде
5. Организовать всё в структурированную вики
6. Включить интеллектуальное взаимодействие через функцию "Спросить"
7. Обеспечить углубленный анализ через DeepResearch

```mermaid
graph TD
    A[Пользователь вводит ссылку на репозиторий] --> AA{Приватный репозиторий?}
    AA -->|Да| AB[Добавить токен доступа]
    AA -->|Нет| B[Клонировать репозиторий]
    AB --> B
    B --> C[Анализ структуры кода]
    C --> D[Создание эмбеддингов]

    D --> M{Выбор провайдера модели}
    M -->|Google Gemini| E1[Генерация через Gemini]
    M -->|OpenAI| E2[Генерация через OpenAI]
    M -->|OpenRouter| E3[Генерация через OpenRouter]
    M -->|Локальная Ollama| E4[Генерация через Ollama]
    M -->|Azure| E5[Генерация через Azure]

    E1 --> E[Создание документации]
    E2 --> E
    E3 --> E
    E4 --> E
    E5 --> E

    D --> F[Создание диаграмм]
    E --> G[Формирование вики]
    F --> G
    G --> H[Интерактивная DeepWiki]

    classDef process stroke-width:2px;
    classDef data stroke-width:2px;
    classDef result stroke-width:2px;
    classDef decision stroke-width:2px;

    class A,D data;
    class AA,M decision;
    class B,C,E,F,G,AB,E1,E2,E3,E4,E5 process;
    class H result;
```

## 🛠️ Структура проекта

```
deepwiki/
├── api/                        # Backend API сервер
│   ├── main.py                 # Точка входа (uvicorn)
│   ├── api.py                  # FastAPI приложение, REST/WebSocket эндпоинты
│   ├── gitlab_auth.py          # GitLab OAuth2 SSO, JWT, MCP-токены
│   ├── gitlab_permission.py    # Проверка прав доступа к репозиториям + кэш
│   ├── admin.py                # API маршруты администратора
│   ├── batch_indexer.py        # Фоновая пакетная индексация
│   ├── mcp_server.py           # MCP-сервер (JWT-аутентификация)
│   ├── metadata_store.py       # JSON-хранилище метаданных индексов
│   ├── product_manager.py      # CRUD для продуктов
│   ├── repo_relations.py       # Анализ зависимостей репозиториев
│   ├── insight_extractor.py    # Извлечение структурированных знаний
│   ├── wiki_generator.py       # Основная логика генерации вики
│   ├── rag.py                  # RAG для одного репозитория
│   ├── multi_rag.py            # Мульти-репозиторный RAG
│   ├── data_pipeline.py        # Клонирование репозиториев, эмбеддинги
│   ├── config.py               # Загрузчик конфигурации, переменные окружения
│   ├── prompts.py              # Шаблоны LLM-промптов
│   ├── config/                 # JSON-файлы конфигурации
│   └── *_client.py             # Клиенты LLM-провайдеров
│
├── src/                        # Клиентское приложение на Next.js
│   ├── app/
│   │   ├── page.tsx            # Главная (SSO вход, список проектов)
│   │   ├── [owner]/[repo]/     # Просмотр вики
│   │   ├── admin/              # Панель администратора
│   │   ├── admin/relations/    # Граф зависимостей репозиториев
│   │   ├── ask/                # Глобальный Ask (кросс-репозиторные вопросы)
│   │   └── auth/callback/      # OAuth callback
│   ├── components/             # React-компоненты
│   └── contexts/               # Контексты Auth, Language
│
├── public/                     # Статические ресурсы
├── package.json                # JS-зависимости
└── .env                        # Переменные окружения (создайте этот файл)
```

## 🤖 Система выбора моделей по провайдерам

DeepWiki поддерживает гибкую систему выбора моделей от разных поставщиков:

### Поддерживаемые провайдеры и модели

- **Google**: По умолчанию `gemini-2.5-flash`, также доступны `gemini-2.5-flash-lite`, `gemini-2.5-pro`, и др.
- **OpenAI**: По умолчанию `gpt-5-nano`, также поддерживает `gpt-5`, `4o` и другие
- **OpenRouter**: Доступ к множеству моделей через единый API (Claude, Llama, Mistral и др.)
- **Azure OpenAI**: По умолчанию `gpt-4o`, поддерживаются и другие
- **Ollama**: Локальные open-source модели, например `llama3`

### Переменные окружения

Каждому провайдеру соответствуют свои ключи:

```bash
GOOGLE_API_KEY=...         # Для моделей Google Gemini
OPENAI_API_KEY=...         # Для моделей OpenAI
OPENROUTER_API_KEY=...     # Для моделей OpenRouter
AZURE_OPENAI_API_KEY=...   # Для моделей Azure
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_VERSION=...

# Кастомный адрес для OpenAI API
OPENAI_BASE_URL=https://ваш-кастомный-api/v1

# Хост Ollama
OLLAMA_HOST=http://localhost:11434

# Каталог конфигурации
DEEPWIKI_CONFIG_DIR=/путь/к/конфигурации
```

### Конфигурационные файлы

DeepWiki использует JSON-файлы для настройки:

1. **`generator.json`** — конфигурация генерации текста и моделей
2. **`embedder.json`** — настройки эмбеддингов и ретривера
3. **`repo.json`** — правила обработки репозиториев

По умолчанию хранятся в `api/config/`, путь можно изменить через `DEEPWIKI_CONFIG_DIR`.

### Кастомизация для сервис-провайдеров

Функция выбора модели позволяет:

- Предоставлять выбор моделей пользователям вашей системы
- Легко адаптироваться к новым LLM без изменения кода
- Поддерживать кастомные или специализированные модели

Пользователи могут выбрать модель через интерфейс или указать свой идентификатор.

### Настройка OpenAI base_url для корпоративных клиентов

Позволяет:

- Использовать приватные API OpenAI
- Подключаться к self-hosted решениям
- Интегрироваться с совместимыми сторонними сервисами

**Скоро**: DeepWiki получит режим, в котором пользователи будут указывать свои API-ключи напрямую в запросах — удобно для корпоративных решений.

## 🧩 Использование совместимых с OpenAI моделей (например, Alibaba Qwen)

Чтобы использовать модели эмбеддингов, совместимые с OpenAI:

1. Замените `api/config/embedder.json` на `embedder_openai_compatible.json`
2. В `.env` добавьте:
```bash
OPENAI_API_KEY=ваш_ключ
OPENAI_BASE_URL=совместимый_endpoint
```

Программа автоматически подставит значения из переменных окружения.

### Логирование

DeepWiki использует стандартный `logging` из Python. Настраивается через:

| Переменная        | Описание                                      | Значение по умолчанию         |
|------------------|-----------------------------------------------|-------------------------------|
| `LOG_LEVEL`      | Уровень логов (DEBUG, INFO, WARNING и т.д.)   | INFO                          |
| `LOG_FILE_PATH`  | Путь к файлу логов                             | `api/logs/application.log`    |

Пример:
```bash
export LOG_LEVEL=DEBUG
export LOG_FILE_PATH=./debug.log
python -m api.main
```

Или через Docker Compose:
```bash
LOG_LEVEL=DEBUG LOG_FILE_PATH=./debug.log docker-compose up
```

Для постоянства логов при перезапуске контейнера `api/logs` монтируется в `./api/logs`.

Также можно указать переменные в `.env`:

```bash
LOG_LEVEL=DEBUG
LOG_FILE_PATH=./debug.log
```

И просто выполнить:

```bash
docker-compose up
```

**Безопасность логов:** в продакшене важно настроить права доступа к `api/logs`, чтобы исключить несанкционированный доступ или запись.

## 🛠️ Расширенная настройка

### Переменные окружения

| Переменная | Назначение | Обязательно | Примечание |
|---|---|---|---|
| **Провайдеры LLM** ||||
| `GOOGLE_API_KEY` | Ключ API для Google Gemini | Нет | Для моделей Gemini и эмбеддингов Google |
| `OPENAI_API_KEY` | Ключ API для OpenAI | Условно | Обязателен при использовании эмбеддингов или моделей OpenAI |
| `OPENROUTER_API_KEY` | Ключ API для OpenRouter | Нет | Для моделей OpenRouter |
| `AZURE_OPENAI_API_KEY` | Ключ Azure OpenAI | Нет | Для моделей Azure OpenAI |
| `AZURE_OPENAI_ENDPOINT` | Endpoint Azure OpenAI | Нет | Для моделей Azure OpenAI |
| `AZURE_OPENAI_VERSION` | Версия API Azure OpenAI | Нет | Для моделей Azure OpenAI |
| `OLLAMA_HOST` | Хост Ollama (по умолчанию: http://localhost:11434) | Нет | Для внешнего сервера Ollama |
| `DEEPWIKI_EMBEDDER_TYPE` | Тип эмбеддера: `openai`, `google`, `ollama`, `bedrock` | Нет | По умолчанию: `openai` |
| **AWS Bedrock** ||||
| `AWS_ACCESS_KEY_ID` | Ключ доступа AWS | Нет | Для Bedrock без ролевой авторизации |
| `AWS_SECRET_ACCESS_KEY` | Секретный ключ AWS | Нет | Для Bedrock без ролевой авторизации |
| `AWS_REGION` | Регион AWS (по умолчанию: `us-east-1`) | Нет | |
| `AWS_ROLE_ARN` | ARN роли AWS для AssumeRole | Нет | Если указан, используется STS AssumeRole |
| **Корпоративный GitLab** ||||
| `GITLAB_URL` | URL экземпляра GitLab | Нет | Для SSO и корпоративных функций |
| `GITLAB_CLIENT_ID` | ID OAuth2-приложения | Нет | Для GitLab SSO |
| `GITLAB_CLIENT_SECRET` | Секрет OAuth2-приложения | Нет | Для GitLab SSO |
| `GITLAB_SERVICE_TOKEN` | Токен сервисного аккаунта | Нет | Для пакетной индексации и доступа к MCP |
| `JWT_SECRET_KEY` | Секрет для подписи JWT | Нет | Обязателен при включённом SSO |
| `ADMIN_USERNAMES` | Имена администраторов через запятую | Нет | Управление доступом к панели администратора |
| `PERMISSION_CACHE_TTL` | TTL кэша прав доступа (секунды) | Нет | По умолчанию: 300 |
| `FRONTEND_ORIGIN` | URL фронтенда для OAuth-колбэков | Нет | По умолчанию: http://localhost:3000 |
| **Сервер** ||||
| `PORT` | Порт API-сервера (по умолчанию: 8001) | Нет | |
| `SERVER_BASE_URL` | URL бэкенда для прокси фронтенда | Нет | По умолчанию: http://localhost:8001 |
| `DEEPWIKI_CONFIG_DIR` | Каталог конфигурации | Нет | По умолчанию: `api/config/` |
| `DEEPWIKI_AUTH_MODE` | Включает режим авторизации (`true`/`1`) | Нет | Простая авторизация для развёртываний без SSO |
| `DEEPWIKI_AUTH_CODE` | Код авторизации для генерации вики | Нет | Только при `DEEPWIKI_AUTH_MODE` |

**Требования к API-ключам:**
- При `DEEPWIKI_EMBEDDER_TYPE=openai` (по умолчанию): требуется `OPENAI_API_KEY`
- При `DEEPWIKI_EMBEDDER_TYPE=google`: требуется `GOOGLE_API_KEY`
- При `DEEPWIKI_EMBEDDER_TYPE=ollama`: API-ключ не нужен (локальная обработка)
- При `DEEPWIKI_EMBEDDER_TYPE=bedrock`: требуются учётные данные AWS (или ролевая авторизация)

Остальные API-ключи нужны только при использовании моделей соответствующих провайдеров.

## Режим авторизации

DeepWiki может быть запущен в режиме авторизации — для генерации вики потребуется ввести секретный код. Это полезно, если вы хотите ограничить доступ к функциональности.

Для включения:

- `DEEPWIKI_AUTH_MODE=true`
- `DEEPWIKI_AUTH_CODE=секретный_код`

Это ограничивает доступ с фронтенда и защищает кэш, но не блокирует прямые вызовы API.

### Запуск через Docker

Вы можете использовать Docker:

#### Запуск контейнера

```bash
docker pull ghcr.io/asyncfuncai/deepwiki-open:latest

docker run -p 8001:8001 -p 3000:3000 \
  -e GOOGLE_API_KEY=... \
  -e OPENAI_API_KEY=... \
  -e OPENROUTER_API_KEY=... \
  -e OLLAMA_HOST=... \
  -e AZURE_OPENAI_API_KEY=... \
  -e AZURE_OPENAI_ENDPOINT=... \
  -e AZURE_OPENAI_VERSION=... \
  -v ~/.adalflow:/root/.adalflow \
  ghcr.io/asyncfuncai/deepwiki-open:latest
```

Каталог `~/.adalflow` содержит:

- Клонированные репозитории
- Эмбеддинги и индексы
- Сгенерированные кэшированные вики

#### Docker Compose

```bash
# Убедитесь, что .env заполнен
docker-compose up
```

#### Использование .env

```bash
echo "GOOGLE_API_KEY=..." > .env
...
docker run -p 8001:8001 -p 3000:3000 \
  -v $(pwd)/.env:/app/.env \
  -v ~/.adalflow:/root/.adalflow \
  ghcr.io/asyncfuncai/deepwiki-open:latest
```

#### Локальная сборка Docker-образа

```bash
git clone https://github.com/AsyncFuncAI/deepwiki-open.git
cd deepwiki-open

docker build -t deepwiki-open .

docker run -p 8001:8001 -p 3000:3000 \
  -e GOOGLE_API_KEY=... \
  -e OPENAI_API_KEY=... \
  ... \
  deepwiki-open
```

#### Самоподписанные сертификаты

1. Создайте каталог `certs` (или свой)
2. Поместите сертификаты `.crt` или `.pem`
3. Соберите образ:

```bash
docker build --build-arg CUSTOM_CERT_DIR=certs .
```

### Описание API

Сервер API:

- Клонирует и индексирует репозитории
- Реализует RAG
- Поддерживает потоковую генерацию

См. подробности в [API README](./api/README.md)

## 🔌 Интеграция с OpenRouter

Платформа [OpenRouter](https://openrouter.ai/) предоставляет доступ ко множеству моделей:

- **Много моделей**: OpenAI, Anthropic, Google, Meta и др.
- **Простая настройка**: достаточно API-ключа
- **Гибкость и экономия**: выбирайте модели по цене и производительности
- **Быстрое переключение**: без изменения кода

### Как использовать

1. Получите ключ на [OpenRouter](https://openrouter.ai/)
2. Добавьте `OPENROUTER_API_KEY=...` в `.env`
3. Активируйте в интерфейсе
4. Выберите модель (например GPT-4o, Claude 3.5, Gemini 2.0 и др.)

Подходит для:

- Тестирования разных моделей без регистрации в каждом сервисе
- Доступа к моделям в регионах с ограничениями
- Сравнения производительности
- Оптимизации затрат

## 🤖 Возможности Ask и DeepResearch

### Ask

- **Ответы по коду**: AI использует содержимое репозитория
- **RAG**: подбираются релевантные фрагменты
- **Потоковая генерация**: ответы формируются в реальном времени
- **История общения**: поддерживается контекст

### DeepResearch

Функция глубокого анализа:

- **Многошаговый подход**: AI сам исследует тему
- **Этапы исследования**:
  1. План
  2. Промежуточные результаты
  3. Итоговый вывод

Активируется переключателем "Deep Research".

## 🏢 Корпоративная интеграция с GitLab

DeepWiki поддерживает полноценное корпоративное развёртывание с GitLab в качестве провайдера аутентификации и репозиториев.

### Настройка GitLab SSO

1. Создайте OAuth2-приложение в GitLab (Admin > Applications):
   - **Redirect URI**: `http://ваш-фронтенд:3000/auth/gitlab/callback`
   - **Scopes**: `read_user`, `read_api`
2. Установите переменные окружения:
   ```bash
   GITLAB_URL=https://gitlab.example.com
   GITLAB_CLIENT_ID=id_вашего_приложения
   GITLAB_CLIENT_SECRET=секрет_вашего_приложения
   JWT_SECRET_KEY=ваш_случайный_секрет
   FRONTEND_ORIGIN=http://ваш-фронтенд:3000
   ADMIN_USERNAMES=admin_user1,admin_user2
   ```
3. Для пакетной индексации и доступа к MCP-серверу создайте сервисный токен с правами `read_api`:
   ```bash
   GITLAB_SERVICE_TOKEN=glpat-xxxxxxxxxxxx
   ```

### Панель администратора

Доступна по адресу `/admin` для пользователей из `ADMIN_USERNAMES`:
- **Проиндексированные проекты**: Просмотр, переиндексация или удаление проиндексированных репозиториев
- **Пакетная индексация**: Выбор и индексация нескольких проектов GitLab одновременно
- **Продукты**: Объединение репозиториев в логические продукты для кросс-репозиторного анализа
- **Статистика системы**: Размеры кэша, статус индексации, обзор конфигурации

### Связи репозиториев

Доступны по адресу `/admin/relations`:
- Автоматическое обнаружение зависимостей через LLM-анализ импортов
- Интерактивный граф зависимостей (ReactFlow) с режимами просмотра: по группам, фокус, полный вид
- Фильтрация связей и визуализация кросс-репозиторных зависимостей

## 🔌 Интеграция MCP-сервера

DeepWiki предоставляет аутентифицированную точку доступа [MCP](https://modelcontextprotocol.io/) по адресу `/mcp`, позволяя внешним ИИ-агентам использовать вашу проиндексированную кодовую базу.

### Доступные инструменты

| Инструмент | Описание |
|------------|----------|
| `list_products` | Список всех продуктов с их репозиториями |
| `get_product_overview` | Сводный обзор по всем репозиториям продукта |
| `search_product_code` | Семантический поиск кода по всем репозиториям продукта |
| `ask_product` | Задать вопрос по всем репозиториям продукта |
| `list_projects` | Список всех проиндексированных проектов со статусом |
| `get_wiki_summary` | Получить структуру вики и заголовки страниц |
| `get_wiki_page` | Прочитать полное содержимое страницы вики |
| `search_code` | Семантический поиск кода в одном проекте |
| `get_repo_relations` | Получить зависимости между репозиториями |
| `ask_question` | Задать вопрос о кодовой базе одного проекта |
| `get_project_insights` | Получить структурированный индекс знаний |
| `extract_project_insights` | Извлечь инсайты с помощью LLM |
| `get_product_insights` | Агрегированные инсайты по продукту |

### Подключение Claude Code

1. Войдите в DeepWiki через GitLab SSO
2. Нажмите на иконку ключа в панели навигации, чтобы получить MCP-токен
3. Выполните сгенерированную команду:
   ```bash
   claude mcp add --transport http deepwiki http://ваш-сервер:8001/mcp \
     --header "Authorization: Bearer <ваш-mcp-токен>"
   ```
4. Теперь Claude Code может обращаться к вашей проиндексированной кодовой базе

## 📱 Скриншоты

![Интерфейс](screenshots/Interface.png)  
*Основной интерфейс DeepWiki*

![Приватный доступ](screenshots/privaterepo.png)  
*Доступ к приватным репозиториям*

![DeepResearch](screenshots/DeepResearch.png)  
*DeepResearch анализирует сложные темы*

### Видео-демо

[![Видео](https://img.youtube.com/vi/zGANs8US8B4/0.jpg)](https://youtu.be/zGANs8US8B4)

## ❓ Решение проблем

### Проблемы с API-ключами

- **“Отсутствуют переменные окружения”** — проверьте `.env`
- **“Неверный ключ”** — уберите пробелы
- **“Ошибка OpenRouter API”** — проверьте ключ и баланс
- **“Ошибка Azure API”** — проверьте ключ, endpoint и версию

### Проблемы с подключением

- **“Нет подключения к API”** — убедитесь, что сервер запущен на 8001
- **“CORS ошибка”** — пробуйте запускать frontend и backend на одной машине

### Ошибки генерации

- **“Ошибка генерации вики”** — попробуйте меньший репозиторий
- **“Неверный формат ссылки”** — используйте корректные ссылки
- **“Нет структуры репозитория”** — проверьте токен доступа
- **“Ошибка диаграмм”** — система попытается автоматически исправить

### Универсальные советы

1. Перезапустите frontend и backend
2. Проверьте консоль браузера
3. Проверьте логи API

## 🤝 Участие

Вы можете:

- Заводить issues
- Отправлять pull requests
- Делиться идеями

## 📄 Лицензия

Проект распространяется под лицензией MIT. См. файл [LICENSE](LICENSE)

## ⭐ История звёзд

[![График звёзд](https://api.star-history.com/svg?repos=AsyncFuncAI/deepwiki-open&type=Date)](https://star-history.com/#AsyncFuncAI/deepwiki-open&Date)
