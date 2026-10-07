# Highlight Studio v10.9.2 — Local Web Launcher

Версия добавляет явное разделение двух режимов Windows-запуска.

## Desktop

`commands/launch/START_DESKTOP.bat` запускает автономный однопользовательский режим с локальной SQLite-базой задач. Регистрация и PostgreSQL не требуются.

## Local Web

`commands/launch/START_WEB_LOCAL.bat` автоматически:

1. проверяет Python и зависимости;
2. проверяет Docker Desktop и запускает его при необходимости;
3. создаёт локальный секрет PostgreSQL в `.highlight_studio/web_local.env`;
4. запускает PostgreSQL только на `127.0.0.1:54329`;
5. ждёт готовности базы;
6. применяет Alembic-миграции;
7. включает web accounts, регистрацию и RBAC;
8. выбирает свободный порт 8010–8099;
9. открывает экран регистрации/входа.

Первый зарегистрированный пользователь получает глобальную роль `admin`. Следующие пользователи получают роль `user`. Внутри проекта используются роли `owner`, `editor`, `viewer`.

PostgreSQL хранится в Docker volume и не удаляется при обычной остановке. `commands/launch/STOP_WEB_LOCAL.bat` останавливает контейнер без удаления аккаунтов и данных.

Desktop-проекты находятся в `projects`, web-проекты — в `projects_web`. Это исключает случайное смешивание автономного и многопользовательского режимов.

## Production

Локальный launcher работает по HTTP только на loopback и поэтому ставит `Secure=0` для cookies. Production deployment по-прежнему обязан работать через Caddy/HTTPS с `Secure=1`.
