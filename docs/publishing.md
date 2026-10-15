# Публикация JobRadar в GitHub

Выполняйте команды из корня проекта. Локальный веб-сервер и личная база могут
оставаться на компьютере: они не нужны для публикации исходников.

## Что входит в репозиторий

- Код, HTML/CSS/JS, тесты и минимальные JSON-фикстуры.
- Публичные `config/*.example.yaml` и `.env.example`.
- README, инструкции, исторический отчёт проверок и скриншоты панели.
- `pyproject.toml`, проверенные версии зависимостей и workflow CI.

`.gitignore` исключает `data/`, `exports/`, базы SQLite, `.env` и его варианты,
личные `config/sources.yaml` и `config/profiles.yaml`, окружения, кеши, логи и ключи.
`.gitignore` не удаляет файлы, уже попавшие в историю; проверяйте состав коммита.

## Проверки перед отправкой

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest -q --cov=jobradar --cov-report=term-missing
git status --short
git diff --cached --stat
git diff --cached --check
git ls-files
```

При необходимости повторного добавления изменений: `git add .`. Перед коммитом
проверьте автора командами `git config user.name` и `git config user.email`.
Если они не настроены, задайте свои значения локально для этого проекта:

```powershell
git config user.name "YOUR_NAME"
git config user.email "YOUR_GITHUB_EMAIL"
git commit -m "Initial JobRadar MVP"
```

Замените `YOUR_NAME` и `YOUR_GITHUB_EMAIL` своими данными. Для конфиденциальности
можно использовать свой адрес noreply из настроек GitHub. Эти значения входят
в историю коммитов. Если первый коммит уже создан, повторять его не нужно.

## Первая отправка

Создайте в своём аккаунте GitHub пустой репозиторий `JobRadar`, выбрав нужную
видимость. Не добавляйте README, `.gitignore` и лицензию при создании: README
и правила игнорирования уже есть локально, а лицензию следует выбрать отдельно.
Лицензия проекту автоматически не назначается.

Подставьте свой логин вместо `YOUR_GITHUB_LOGIN`:

```powershell
git remote add origin https://github.com/YOUR_GITHUB_LOGIN/JobRadar.git
git push -u origin main
```

Если `origin` уже существует, сначала проверьте адрес через `git remote -v`.
Для отправки используйте обычную авторизацию Git Credential Manager либо SSH.
Не вставляйте токены в URL репозитория или файлы проекта.

После push проверьте вкладку Actions: удалённый CI выполняется впервые только
после отправки. Прохождение локальных проверок не подтверждает результат CI.

## Установка из опубликованного репозитория

```powershell
git clone https://github.com/YOUR_GITHUB_LOGIN/JobRadar.git
cd JobRadar
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements-lock.txt
.\.venv\Scripts\python.exe -m jobradar serve --open
```

Приложение использует публичные примеры конфигурации, пока пользователь не сохранит
свои настройки. Рабочая база создаётся при первом запуске; вакансии можно загрузить
кнопкой «Собрать вакансии» или CLI-командой `collect`.
