# Возможности KSM в ядре 7.2.9-arch1-1

Источники: `Documentation/admin-guide/mm/ksm.rst` и `Documentation/filesystems/proc.rst` из пакета `linux-docs 7.2.9.arch1-1` (версия совпадает с работающим ядром), а также `/usr/include/linux/prctl.h`, `/proc/config.gz`, содержимое `/sys/kernel/mm/ksm/` (снято в `env/vm-oracle/ksm-sysfs.txt`).

Обозначения в столбце «Проверено»: **sysfs** — файл присутствует в системе; **эксп.** — поведение проверено экспериментом этапа 0 (см. `docs/stage-0.md`); **док.** — только по документации.

| Возможность | Есть в 7.2.9 | Проверено | Комментарий |
|---|---|---|---|
| `CONFIG_KSM=y` | да | `/proc/config.gz` | KSM встроен в ядро |
| `madvise(MADV_MERGEABLE / MADV_UNMERGEABLE)` | да | эксп. | Классический способ: приложение само отмечает область памяти |
| `prctl(PR_SET_MEMORY_MERGE = 67)` / `PR_GET_MEMORY_MERGE = 68` | да | эксп. | KSM для всего процесса (все подходящие анонимные области). Константы в `prctl.h`. **В ksm.rst и man-pages 6.19 не описан**, семантика взята из коммитов ядра (см. `docs/references.md`) и проверена экспериментально |
| Наследование prctl через fork / exec | да | эксп. | См. `docs/decisions.md`, раздел про prctl. В истории ядра были ошибки именно в этом месте (исправления в 6.7, 6.10 и в 2025 г.) |
| `/proc/<pid>/ksm_stat` | да | эксп. | Поля `ksm_rmap_items`, `ksm_zero_pages`, `ksm_merging_pages`, `ksm_process_profit`, `ksm_merge_any` (стоит ли prctl), `ksm_mergeable` (есть ли у процесса зарегистрированные области) |
| `/proc/<pid>/ksm_merging_pages` | да | эксп. | Число страниц процесса, участвующих в слиянии |
| `smart_scan` | да, включён (=1) | sysfs | Пропуск страниц, которые раньше не удалось слить; эффективность видна в `pages_skipped` |
| `advisor_mode` (`none` / `scan-time`) | да, `none` | sysfs | Автоподстройка `pages_to_scan` под целевое время полного прохода (`advisor_target_scan_time`=200 с, лимит CPU `advisor_max_cpu`=70 %) |
| `general_profit` | да | sysfs | Оценка выгоды: сэкономленные страницы минус расход на `rmap_item` (64 Б на просканированную страницу на x86-64) |
| `ksm_zero_pages`, `use_zero_pages` | да, `use_zero_pages=0` | sysfs | При `use_zero_pages=1` нулевые страницы сливаются с системной нулевой страницей; экономия = `pages_sharing + ksm_zero_pages` |
| `max_page_sharing` | да, 256 | sysfs | Ограничение числа ссылок на одну KSM-страницу (защищает от долгих обходов rmap). Влияет на максимальное отношение `pages_sharing/pages_shared` |
| `stable_node_chains`, `stable_node_dups` | да | sysfs | Счётчики для страниц, упёршихся в `max_page_sharing` |
| Счётчики `cow_ksm`, `ksm_swpin_copy` в `/proc/vmstat` | да | sysfs | `cow_ksm` — число copy-on-write копирований KSM-страниц. **Ключевая метрика для уровня 2 и 3 работы** (связь всплесков задержки с COW) |
| `merge_across_nodes` | да, 1 | sysfs | Для одного NUMA-узла (наш случай) не важно |
| systemd `MemoryKSM=` | да (systemd 262, опция с v254) | док. | systemd вызывает тот же prctl для процессов юнита. Кандидат на способ включения KSM для контейнеров |

## Параметры по умолчанию (на момент фиксации)

`run=0`, `pages_to_scan=100`, `sleep_millisecs=20`, `smart_scan=1`, `advisor_mode=none`, `max_page_sharing=256`, `use_zero_pages=0`, `merge_across_nodes=1`.

По документации значения `pages_to_scan=100` и `sleep_millisecs=20` выбраны «для демонстрации». Это 100 страниц × 50 проходов/с ≈ 5000 страниц/с ≈ 19.5 МиБ/с. На большом объёме памяти такой KSM сходится медленно, поэтому параметры сканирования будут явным фактором эксперимента.

## Что важно для методики

- **THP = `always`** (`CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS=y`). KSM сливает только 4-КиБ страницы, и huge-страницы (2 МиБ) ему приходится разбивать. Режим THP влияет и на скорость слияния, и на задержки. Его надо фиксировать в каждом прогоне и, вероятно, сделать фактором эксперимента.
- **swap = zram 4 ГиБ** (zstd). KSM-страницы могут уходить в swap, а при возврате слияние разрушается (`ksm_swpin_copy`). На этапе 0 памяти с запасом, и swap не используется.
- KSM сливает **только анонимную приватную память**, page cache не трогает.

## Нативная система: ядро 7.3.4-arch1-2 (снято 2026-11-18)

Источники: `Documentation/admin-guide/mm/ksm.rst` и `filesystems/proc.rst` из `linux-docs 7.3.4.arch1-2` (совпадает с работающим ядром), `/proc/config.gz`, `/usr/include/linux/prctl.h`, `env/native/ksm-sysfs.txt`.

- **Набор файлов `/sys/kernel/mm/ksm/` совпадает с 7.2.9 поимённо** (сравнение `env/vm-oracle/ksm-sysfs.txt` и `env/native/ksm-sysfs.txt`). Значения по умолчанию те же: `run=0`, `pages_to_scan=100`, `sleep_millisecs=20`, `smart_scan=1`, `advisor_mode=none`, `max_page_sharing=256`, `use_zero_pages=0`.
- `CONFIG_KSM=y`, `CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS=y`; `PR_SET_MEMORY_MERGE=67` / `PR_GET_MEMORY_MERGE=68` в `prctl.h`; счётчики `cow_ksm`, `ksm_swpin_copy` в `/proc/vmstat`; поля `ksm_stat` по `proc.rst` — те же шесть.
- `ksm.rst` 7.3.4 по-прежнему **не описывает `PR_SET_MEMORY_MERGE`**: наследование через fork/exec и в podman проверяется опытом заново (серия `inherit`, этап 3).
- Tracepoint-ы `ksm:*` из-под обычного пользователя не видны (tracefs только для root): проверяются `preflight.sh` и серией `trace-check` через sudo.
