# Источники

Источники, на которые опираются методические решения. Для каждого указано, где он использован.

## Документация ядра Linux (версия 7.2.9, пакет `linux-docs`, совпадает с работающим ядром)

1. **Documentation/admin-guide/mm/ksm.rst** — Kernel Samepage Merging: madvise, sysfs-интерфейс (`run`, `pages_to_scan`, `sleep_millisecs`, `smart_scan`, `advisor_mode`, `max_page_sharing`, `use_zero_pages`), счётчики (`pages_shared`, `pages_sharing`, `general_profit`, `ksm_zero_pages`), события `cow_ksm` и `ksm_swpin_copy` в `/proc/vmstat`. Локально: `/usr/lib/modules/7.2.9-arch1-1/build/Documentation/admin-guide/mm/ksm.rst`. Онлайн: https://docs.kernel.org/admin-guide/mm/ksm.html
   — использовано: `env/ksm-features.md`, интерпретация счётчиков в сэмплере и сводках, теоретическое ожидание экономии.
2. **Documentation/filesystems/proc.rst, раздел 3.14 `/proc/<pid>/ksm_stat`** — поля `ksm_rmap_items`, `ksm_zero_pages`, `ksm_merging_pages`, `ksm_process_profit`, `ksm_merge_any`, `ksm_mergeable`.
   — использовано: проверка наследования prctl, сэмплер.
3. **include/uapi/linux/prctl.h** — `PR_SET_MEMORY_MERGE = 67`, `PR_GET_MEMORY_MERGE = 68`. Прочих описаний prctl для KSM в документации ядра 7.2.9 и в man-pages 6.19 нет (проверено поиском).

## Коммиты ядра (история prctl для KSM)

4. **d7597f59d1d3 "mm: add new api to enable ksm per process"**, Stefan Roesch, Linux 6.4 (2023). Вводит `PR_SET_MEMORY_MERGE`: KSM включается для всех подходящих VMA процесса, флаг `MMF_VM_MERGE_ANY` на mm.
5. **3c6f33b7273a "mm/ksm: support fork/exec for prctl"**, Stefan Roesch (2023). Сохранение флага при fork и exec. Тест: 0374af1da077 "mm/ksm: Test case for prctl fork/exec workflow" — https://lkml.iu.edu/hypermail/linux/kernel/2309.2/04734.html
6. **3a9e567ca45f "mm/ksm: fix ksm exec support for prctl"**, Jinjiang Tu, Linux 6.10 (2024). После exec флаг сохранялся, но процесс не попадал в список сканирования ksmd. Исправлено. https://android-kvm.googlesource.com/linux/+/3a9e567ca45fb5280065283d10d9a11f0db61d2b
7. **"[PATCH linux-next v2 1/2] mm/ksm: fix exec/fork inheritance support for prctl"**, xu xin, 2025-10-07. При очень частом сканировании ksmd мог сбросить `MMF_VM_MERGE_ANY` у нового процесса до того, как тот выделил память, и флаг терялся. https://lkml.iu.edu/2510.0/04801.html. Вошёл ли патч в 7.2.9, по первоисточнику не подтверждено, поэтому проверено экспериментом (конфигурация `aggressive` в шаге 6).
   — использовано (4–7): дизайн эксперимента шага 6, `docs/decisions.md`.

## Прочее

8. **systemd.exec(5), опция `MemoryKSM=`** (systemd ≥ 254, у нас 262). Включает KSM для процессов юнита через тот же prctl. Локально: `man 5 systemd.exec`.
9. **LWN: "KSM: Kernel Samepage Merging"** (исходная статья), https://lwn.net/Articles/306704/ и https://lwn.net/Articles/330589/ — ссылки из ksm.rst; общая идея и история.
10. **rt-tests / cyclictest** — стандартная методика измерения задержки пробуждения по таймеру (абсолютный `clock_nanosleep`, опоздание относительно расписания). https://wiki.linuxfoundation.org/realtime/documentation/howto/tools/cyclictest/start
    — использовано: дизайн `workloads/jitter/timer_jitter.c`.
11. **Исходный код cyclictest v2.11**, `src/cyclictest/cyclictest.c`, https://git.kernel.org/pub/scm/utils/rt-tests/rt-tests.git/plain/src/cyclictest/cyclictest.c?h=v2.11 (скачан 2026-10-16). Пропуск прошедших периодов (`while (tsgreater(&now, &next))` в timerthread), включение SCHED_FIFO любым `-p` (обработка `OPT_PRIORITY`), `--laptop` и `/dev/cpu_dma_latency` (`set_latency_target`).
    — использовано: семантика пропущенных тиков в `timer_jitter.c`, команда cyclictest в `scripts/stage0-jitter.sh`, `docs/decisions.md` Р10.
12. **systemd-detect-virt(1)** — определение типа виртуализации (`oracle` = VirtualBox, `none` = нативная система). Использовано в `capture-env.sh` и `runinfo.py`.
13. **Коммиты истории tracepoint-ов и advisor KSM** (проверено по дереву исходников torvalds/linux на kernel.org, 2026-10-17):
    - `739100c88f49` «mm: add tracepoints to ksm», Stefan Roesch, 2023-03-28. Файла `include/trace/events/ksm.h` нет в v6.3 (HTTP 404), он есть в v6.4 → **tracepoint-ы с 6.4**;
    - `5088b49730af` «mm/ksm: add tracepoint for ksm advisor», Stefan Roesch, 2023-12-29. `ksm_advisor` и `advisor_mode` отсутствуют в v6.7 и есть в v6.8 → **advisor и его tracepoint с 6.8**;
    - `PR_SET_MEMORY_MERGE` отсутствует в `include/uapi/linux/prctl.h` v6.3 и есть в v6.4.
    — использовано: `docs/decisions.md` Р12, Р14.
14. **Selftests ядра v7.2.9: `tools/testing/selftests/mm/ksm_functional_tests.c`** (прочитан исходник). Тесты `test_prctl_fork` и `test_prctl_fork_exec` проверяют и флаг `PR_GET_MEMORY_MERGE` в потомке, и фактическое слияние 2 МиБ. fork+exec проверяется при `pages_to_scan=2000`, `sleep_millisecs=0`. → Наследование через fork/exec **уже покрыто тестами ядра** и как вклад работы не подаётся (решение владельца 2026-10-17).
15. **Documentation/mm/ksm.rst** (v7.2.9, проектная документация): цепочки и дубликаты стабильного дерева при достижении `max_page_sharing`; обход rmap линеен по числу разделяющих отображений, поэтому большой `max_page_sharing` даёт «slower worst case for rmap walks … during swapping, compaction, NUMA balancing and page migration». **Documentation/admin-guide/mm/ksm.rst**, раздел «Monitoring KSM events»: «KSM might help save memory, it's a tradeoff by may suffering delay on KSM COW or on swapping in copy» — цена COW названа в самой документации.
    — использовано: Р13, план серий по задержкам.

16. **wrk2** (Gil Tene), https://github.com/giltene/wrk2 — README: «A constant throughput, correct latency recording variant of wrk»; Coordinated Omission — механизм, «through which most of the high latency artifacts exhibited by the measured server will be ignored»; wrk2 меряет задержку от момента, когда запрос должен был уйти по расписанию постоянной частоты. [прочитан README] — использовано: Р21.
17. **PostgreSQL 17, pgbench** — документация https://www.postgresql.org/docs/17/pgbench.html (`--rate`: задержка от запланированного старта, пуассоновское расписание; формат журнала `-l`; `--latency-limit` пропускает опоздавшие) и исходник `src/bin/pgbench/pgbench.c` ветки REL_17_STABLE (в журнал пишется `now − txn_scheduled`). [прочитаны документация и фрагменты кода] — использовано: Р21.

18. **Luanti 5.17** — [`register_globalstep`, `forceload_block`](https://api.luanti.org/core-namespace-reference/), [реализация `get_us_time`](https://raw.githubusercontent.com/luanti-org/luanti/5.17.0/src/script/lua_api/l_util.cpp) и [часы Linux в `porting.h`](https://raw.githubusercontent.com/luanti-org/luanti/5.17.0/src/porting.h). Метки Lua-мода на Linux привязаны к `CLOCK_MONOTONIC_RAW`. Использовано в шаге 2 этапа 4.
19. **Godot 4.7** — [`_physics_process` и логический `delta`](https://docs.godotengine.org/en/stable/classes/class_node.html), [монотонные метки и настенные часы](https://docs.godotengine.org/en/stable/classes/class_time.html), [реализация `get_ticks_usec` на Unix](https://raw.githubusercontent.com/godotengine/godot/4.7-stable/drivers/unix/os_unix.cpp). На Linux движок использует `CLOCK_MONOTONIC_RAW` со смещением от старта процесса. Использовано в шаге 2 этапа 4.

## Связанные работы

Каждая запись сделана по прочитанному первоисточнику (полный текст PDF или HTML), в квадратных скобках — что именно прочитано. PDF статей в репозиторий не кладутся (авторское право), их можно получить по ссылкам.

### R1. UKSM (FAST'18)
Nai Xia, Chen Tian, Yan Luo, Hang Liu, Xiaoliang Wang. *UKSM: Swift Memory Deduplication via Hierarchical and Adaptive Memory Region Distilling.* 16th USENIX Conference on File and Storage Technologies (FAST '18), Oakland, CA, 2018. https://www.usenix.org/conference/fast18/presentation/xia [полный текст PDF]
- Предлагает альтернативу KSM: области памяти ранжируются по выгоде и штрафу дедупликации, «горячие» сканируются чаще, плюс адаптивное частичное хеширование страниц.
- Оценка на **KVM** (три ВМ Ubuntu 16.04) и **Docker** (три контейнера с Apache + MySQL + WordPress из одного образа).
- Память/ЦП: «with the same amount of CPU cycles in the same time envelop, UKSM can achieve up to 12.6× and 5× more memory saving than CBPS approaches on static and dynamic workloads».
- Влияние на производительность хеш-сканера уровня KSM на полной скорости (сканер и нагрузка на отдельных ядрах): 29.7 % для нагрузки с частыми COW (сборка ядра Linux), 1.5–22.9 % для SPEC CPU2006. Причины по профилированию авторов: конкуренция за блокировки управления памятью (VMA, таблицы страниц) и page fault-ы COW на слитых страницах.
- Важно для нас: про Docker сказано «the typical setup of Docker containers (which cannot be handled by KSM)». В 2018 г. KSM работал только через `madvise`, а prctl появился в 6.4 (п. 13). Наш шаг 6 этапа 0 показывает путь для KSM в контейнерах podman.

### R2. XLH (USENIX ATC'13)
Konrad Miller, Fabian Franz, Marc Rittinghaus, Marius Hillenbrand, Frank Bellosa (KIT). *XLH: More Effective Memory Deduplication Scanners Through Cross-layer Hints.* USENIX ATC '13, San Jose, 2013, pp. 279–290. https://www.usenix.org/conference/atc13/technical-sessions/presentation/miller, PDF: https://www.usenix.org/system/files/conference/atc13/atc13-miller.pdf [полный текст PDF]
- Вводит понятие **merge latency**: «the time between establishing certain content in a page and merging it with a duplicate». Для сканеров она велика: «Current scanners need a considerable amount of time to detect new sharing opportunities (e.g., 5 min)».
- При сборке ядра «80% of all encountered sharing opportunities lived between 30 seconds and 5 minutes», поэтому медленный сканер их не успевает поймать.
- Решение: подсказки из слоя ввода-вывода хоста (страницы образа диска ВМ), Linux 3.4 + QEMU/KVM. «XLH saves 290 MiB vs. 75 MiB of main memory for two VMs with 512 MiB assigned memory each».
- Для нас: метрика «время схождения» (этап 0: 33.6 с до 100 % на 256 МиБ при параметрах по умолчанию) — это и есть merge latency в смысле XLH. Авторы пишут «The default scan rate for Linux/KSM is 1000 pages per second». В ядре 7.2.9 по умолчанию номинально 100 страниц / 20 мс = 5000 страниц/с (фактически у нас ≈4100), то есть их значение относится к их системе 2013 г. и нашему ядру не соответствует.

### R3. Para-ksm (USENIX ATC'25)
Houxiang Ji (UIUC), Minho Kim, Seonmu Oh (DGIST), Daehoon Kim (Yonsei), Nam Sung Kim (UIUC). *Para-ksm: Parallelized Memory Deduplication with Data Streaming Accelerator.* USENIX ATC '25, Boston, 2025. https://www.usenix.org/conference/atc25/presentation/ji [полный текст PDF]
- Выносит сравнение страниц (memcmp) и контрольные суммы (xxhash) KSM на ускоритель Intel DSA с пакетной обработкой.
- Мотивация: KSM, работающий на одном ядре с Redis, повышает загрузку ЦП на 14–65 %. memcmp и xxhash занимают 21 % и 17 % циклов KSM.
- Методика: Linux 6.2.15, 40 ВМ по 1 vCPU и 6 ГБ с привязкой к отдельным физическим ядрам, KSM на хосте, `pages_to_scan` 64–2048 (ksmtuned), Redis + YCSB, LLC разделён через Intel CAT.
- **p99 Redis**: «YCSB workloads on Redis experience higher degradation, with an average of 3.7×» для обычного KSM на ЦП (CPU-ksm) относительно системы без KSM. Объяснение авторов: «the tail latency is more sensitive to the CPU contention caused by the intensive data-plane functions running on the CPU». Para-ksm снижает деградацию до 1.3–2.7× (по аннотации).
- Для нас: это измерение p99 с KSM и без него, но в виде агрегированного отношения для KSM хоста между ВМ. Привязки отдельных всплесков к событиям KSM (слияние, COW) в статье мы не нашли.

### R3a. Styx (USENIX ATC'23) — найдено при поиске R3, та же группа
Houxiang Ji, Mark Mansi, Yan Sun, Yifan Yuan, Jinghan Huang, Reese Kuper, Michael M. Swift, Nam Sung Kim. *Styx: Exploiting SmartNIC Capability to Reduce Datacenter Memory Tax.* USENIX ATC '23, Boston, 2023. https://www.usenix.org/conference/atc23/presentation/ji [полный текст PDF]
- Redis + YCSB как «memory-intensive/latency-sensitive application», Linux 5.4: «sys-ksm and sys-zswap increase the p99 latency values by 4.8–9.7× and 8.1–11.0×, respectively, compared to sys-no-mo». С выносом функций KSM на SmartNIC — 1.0–1.1×.

### R4. LWN: обзор KSM по докладу Meta (LPC 2023)
Jake Edge. *An overview of kernel samepage merging (KSM).* LWN.net, 2023-11-29. https://lwn.net/Articles/953141/ [полный текст статьи]. Плюс слайды доклада: Stefan Roesch, *Kernel Samepage Merging (KSM) Usage at Meta and future Improvements*, LPC 2023, https://lpc.events/event/17/contributions/1625/attachments/1320/2649/KSM.pdf [полный текст слайдов]
- Tracepoint-ы: в статье — «prior to the 6.4 kernel, it was not possible to figure out how long the scans were taking. He added some tracepoints to KSM … ksm_start_scan and ksm_stop_scan are the two most important». В слайдах прямо: «The trace events have been added for Kernel Version 6.4. Before version 6.4 no tracepoints existed». Подтверждено по дереву исходников (п. 13).
- prctl: «The new prctl()-based method was added in the 6.4 kernel»; одновременно systemd получил `MemoryKSM`.
- Параметры: «the default pages_to_scan value was set to 100, which is "way too low"». Для Instagram 4000–5000, для других нагрузок 2000–3000. Meta держит полный проход около 2–3 минут. Пример: ~73 000 `pages_shared` и 2.1 млн `pages_sharing`, «a savings of around 6GB of memory on a 64GB machine».
- «Auto-tune» (будущий advisor): цель — заданное время полного прохода. ЦП ksmd до 60 % на старте и около 30 % в устойчивом режиме, экономия ЦП ksmd 20–30 %.
- **Ограничение применимости**: «latency-sensitive workloads are not good candidates for KSM». Это утверждение из доклада, без приведённых измерений. Там же упомянуты известные атаки по сторонним каналам на KSM.
- Для нас: утверждение о задержке сформулировано как практическая рекомендация без данных. Количественная проверка «насколько и почему» и есть уровень 2 нашей работы.

### R5. Документация ядра по KSM
См. п. 1, 2, 15 выше: `max_page_sharing` (ограничение длины обхода rmap и его цена при swap/компакции/миграции), явное упоминание задержки COW как цены KSM.

### R6. Remote Memory-Deduplication Attacks (NDSS 2022)
Martin Schwarzl, Erik Kraft, Moritz Lipp, Daniel Gruss (TU Graz). *Remote Memory-Deduplication Attacks.* NDSS Symposium 2022 (официальная страница: https://www.ndss-symposium.org/ndss-paper/auto-draft-235/); arXiv:2111.08553v1, 2021-11-16 [полный текст PDF arXiv v1]
- Полностью удалённая атака: по времени ответа HTTP/1 и HTTP/2 определяется, произошло ли COW-исключение на сервере. На Linux цель — KSM в ВМ KVM (Ubuntu 20.04 включает KSM для ВМ KVM по умолчанию), Memcached, MariaDB/InnoDB, удалённый обход KASLR.
- **Цена COW, измеренная внутри ВМ KVM**: «the average local timing difference between a regular write memory access and a memory access causing a copy-on-write page fault is 7209.3 ns (n = 100…)»; для 8 и 16 страниц — 13 610.82 и 22 946.14 нс.
- Вывод авторов: «memory deduplication must also be considered a security risk if only applied within a single security domain».
- Для нас: (1) ориентир для серии по цене COW (у нас одиночные прогоны дали 2.5–21 мкс на страницу); (2) COW-задержка измерима даже по сети, то есть задержки от KSM видимы снаружи. Это довод, что их стоит изучать; (3) раздел про безопасность в статье — ограничение применимости.

### R7. User-guided Page Merging для serverless (IEEE BigData 2023)
Wei Qiu (Huawei), Marcin Copik (ETH Zürich), Yun Wang (SJTU), Alexandru Calotoiu, Torsten Hoefler (ETH Zürich). *User-guided Page Merging for Memory Deduplication in Serverless Systems.* arXiv:2311.13588v1, 2023-11-22; на странице arXiv: «Accepted at IEEE BigData 2023» [полный текст PDF arXiv v1]
- Фоновое сканирование KSM «too slow to locate sharing candidates» для короткоживущих функций. Предложен модуль ядра UPM: приложение через `madvise` указывает области, а ядро сливает их синхронно, без сканирования.
- До 55 % экономии памяти на 16 параллельных контейнерах (распознавание изображений). Время на дедупликацию — в среднем 12 % (ResNet) и 42 % (AlexNet) от времени функции, один раз при холодном старте.
- Для нас: та же проблема merge latency, что у XLH, в контейнерной среде; альтернатива сканеру с ценой в задержке старта.

## Позиционирование (рабочая формулировка)

По прочитанным работам вопрос «KSM и задержки» освещён **фрагментарно**:
- экономия памяти и ЦП сканера хорошо изучены (UKSM, XLH, Meta);
- влияние KSM на хвосты задержки измерено как **агрегированная** деградация p99 одного приложения (Redis в ВМ, Para-ksm, Styx), и объясняется конкуренцией за ЦП;
- цена отдельного COW измерена в работах по безопасности (≈7 мкс в ВМ KVM);
- рекомендация «не для чувствительных к задержке нагрузок» (Meta, LWN) дана без измерений.

В прочитанных работах мы не встретили:
- привязки отдельных всплесков задержки к событиям KSM (проходы, слияния, COW) через tracepoint-ы, которые есть с 6.4;
- измерений для приложений с фиксированным тиком;
- KSM для контейнеров через `PR_SET_MEMORY_MERGE` (UKSM в 2018 г. писал, что Docker-контейнеры KSM не обрабатывает).

Это не утверждение «никто не исследовал», а описание прочитанного. Список будет пополняться. Наследование prctl через fork/exec как вклад не подаётся (п. 14).
