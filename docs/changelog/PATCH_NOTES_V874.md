# v8.7.4 Fast Import Picker

- Fixed confusing `404 Видео по этому пути не найдено` for Fast Import by normalizing quoted paths, file:// URLs and URL-encoded paths.
- Added a local disk browser in the UI: click `Обзор диска`, navigate folders, select a video, then click `Импорт по пути`.
- Backend now returns a detailed checked path + hint for missing files instead of a generic 404.
- Added smoke tests for quoted paths and local folder browsing.
