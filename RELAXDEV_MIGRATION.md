# Перенос Нямметра на RelaxDev

## 1. Сначала подтвердить постоянное хранилище

До запуска боевого бота запросить у поддержки RelaxDev:

> Есть ли у Docker-проекта постоянный локальный volume, который сохраняется после редеплоя, рестарта и rollback? Какой путь монтирования использовать для SQLite? Нужен обычный файловый путь, а не объектный Storage API.

Не запускать боевой токен, пока путь постоянного volume не подтверждён.

## 2. Создать тестовый проект

- Репозиторий: `basalaevalexey-ctrl/easy-food`
- Ветка: `main`
- Сборка: существующий `Dockerfile`
- Порт: `3000`
- Автодеплой: выключен до завершения миграции
- Токен: отдельный тестовый бот либо боевой токен только при `TELEGRAM_POLLING_ENABLED=false`

Пример ENV для теста:

```env
BOT_TOKEN=<test_or_production_bot_token>
OPENAI_API_KEY=<openai_key>
ADMIN_IDS=<admin_telegram_id>
OPENAI_MODEL=gpt-4o-mini
DATABASE_PATH=<persistent_volume>/calories.sqlite3
DATABASE_BACKUP_PATHS=<persistent_volume>/backups/calories.sqlite3
DATABASE_REQUIRE_EXISTING=true
DATABASE_MIN_USERS=<число_из_копии>
DATABASE_MIN_ENTRIES=<число_из_копии>
DATABASE_MIN_EVENTS=<число_из_копии>
AUTO_PUSH_TIME=19:00
APP_TIMEZONE=Europe/Moscow
BACKGROUND_JOBS_ENABLED=false
TELEGRAM_POLLING_ENABLED=false
INSTANCE_NAME=relaxdev-staging
PORT=3000
WEBAPP_URL=https://<project>.relaxdev.ru
```

Если база не найдена, повреждена или содержит меньше заданного количества данных, процесс завершится до подключения Telegram polling.

## 3. Подготовить копию базы

1. На действующем боте выполнить `/backup_db`.
2. Сохранить полученный `calories.sqlite3`.
3. Проверить локально:

```powershell
python scripts/verify_database.py path\to\calories.sqlite3
```

4. Загрузить файл точно в `DATABASE_PATH` тестового проекта.
5. Установить минимальные счётчики из результата проверки.

## 4. Проверить тестовый проект

- `GET https://<project>.relaxdev.ru/health` возвращает `status: ok`.
- `GET https://<project>.relaxdev.ru/health?deep=1` возвращает актуальные счётчики и SHA файла.
- `integrity` равно `ok`.
- `users`, `entries`, `events` совпадают с проверенной копией.
- После рестарта проекта SHA и счётчики не уменьшаются.
- После редеплоя проекта SHA и счётчики не уменьшаются.
- Текстовое и фото-распознавание работают с сервера RelaxDev.
- Миниапп загружает профиль, дневник, воду, миссии и достижения.
- Изменения в миниаппе появляются в той же SQLite-базе.

## 5. Боевое переключение

1. Выбрать короткое окно обслуживания.
2. Остановить старый экземпляр бота.
3. Сразу выполнить и скачать финальный `/backup_db`.
4. Проверить финальную базу скриптом.
5. Загрузить её в постоянный volume RelaxDev.
6. Обновить минимальные счётчики в ENV.
7. Поставить боевой `BOT_TOKEN`.
8. Поставить `INSTANCE_NAME=relaxdev-production`.
9. Оставить `TELEGRAM_POLLING_ENABLED=false` и `BACKGROUND_JOBS_ENABLED=false` на первый контрольный запуск.
10. Проверить `/health`, `/admin`, конкретный профиль и последнюю запись еды.
11. Убедиться, что старый бот остановлен.
12. Включить `TELEGRAM_POLLING_ENABLED=true` и `BACKGROUND_JOBS_ENABLED=true`, затем сделать последний редеплой.
13. Обновить `WEBAPP_URL` и домен миниаппа в BotFather.

Старый проект оставить выключенным, но не удалять минимум несколько дней. Один боевой Telegram-токен нельзя одновременно запускать на двух polling-экземплярах.
