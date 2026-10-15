# Работа с кодом

Запускайте команды из корня клонированного репозитория. Нужен Python 3.12+;
локальный запуск описан в [README](README.md).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements-lock.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest -q --cov=jobradar --cov-report=term-missing
node --check jobradar/web/static/app.js
```

В Linux/macOS используйте `.venv/bin/python`. Node.js требуется только для проверки
синтаксиса JavaScript; приложению он не нужен. Обычные тесты используют фикстуры
и моки HTTP и не обращаются к внешним API. Изменения нормализации, фильтров,
пагинации и сохранения снимков сопровождайте регрессионными тестами.

Реальные API проверяются отдельно, только по явному запросу:

```powershell
$env:JOBRADAR_INTEGRATION = "1"
try {
    .\.venv\Scripts\python.exe -m pytest -m integration -q
} finally {
    Remove-Item Env:JOBRADAR_INTEGRATION
}
```

Не записывайте личную конфигурацию поверх `*.example.yaml`. В репозитории должны
оставаться только публичные примеры. Реальные базы, экспорты, `.env`, резервные
копии и виртуальное окружение исключены из Git. Для новых фикстур используйте
минимальные обезличенные ответы; полные выгрузки работодателей не коммитьте.

[CI](.github/workflows/ci.yml) запускает проверки на Windows/Linux с Python
3.12/3.13 при push и pull request, без запросов к публичным boards. Ручной запуск
доступен через Actions → CI → Run workflow. Используются официальные
[checkout](https://github.com/actions/checkout) и
[setup-python](https://github.com/actions/setup-python).

Подготовка и первая отправка описаны в [инструкции публикации](docs/publishing.md).
