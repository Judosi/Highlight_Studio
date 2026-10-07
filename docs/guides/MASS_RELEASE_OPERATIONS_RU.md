# Операционный план массового выпуска

1. Заполнить `docs/templates/vod_benchmark.csv` реальными данными и сформировать отчёт:

```bash
python tools/quality/evaluate_vod_benchmark.py docs/templates/vod_benchmark.csv --output <DATA_DIR>/quality_benchmark_report.json
```

2. Заполнить mass-release evidence через внутренний API или файл `mass_release_evidence.json`.
3. Настроить HTTPS URL лицензии, оплаты, кабинета, поддержки, политик, телеметрии, crash reports и обновлений.
4. Собрать и подписать Windows installer.
5. Проверить Stable и Beta каналы обновления.
6. Провести установку, обновление и удаление на чистых Windows 10/11.
7. Запустить `python tools/release/check_mass_release.py --strict`.
8. Публиковать релиз только при нулевом числе blockers.
