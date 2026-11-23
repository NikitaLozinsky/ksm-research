/*
 * timer_jitter.c — измерение джиттера пробуждения по таймеру (базовая линия шума).
 *
 * Программа имитирует «тик» с фиксированным периодом, как цикл игрового
 * сервера: просыпается в заранее известные моменты T0 + k*период и
 * записывает, на сколько она опоздала относительно запланированного момента:
 *
 *     lateness_k = фактическое_время_пробуждения - (T0 + k*период)
 *
 * Сон делается через clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME): ожидание
 * до абсолютного момента. Поэтому ошибки не накапливаются: опоздание на одном
 * тике не сдвигает расписание следующих. Это тот же принцип, что у утилиты
 * cyclictest (rt-tests).
 *
 * Пропущенные периоды. Если программа проснулась так поздно, что уже прошли
 * следующие запланированные моменты, эти моменты ПРОПУСКАЮТСЯ (не догоняются),
 * а их число записывается в столбец missed. Так же делает cyclictest 2.11
 * (цикл `while (tsgreater(&now, &next))` в timerthread). Без пропуска одна
 * остановка на 100 мс при периоде 1 мс дала бы 100 «опозданий» подряд
 * (100, 99, ... мс) и раздула бы все квантили. С пропуском она даёт одно
 * опоздание 100 мс и missed=99.
 *
 * Опоздание складывается из: точности hrtimer, задержки планировщика
 * (пробуждение → получение ЦП), а в ВМ ещё и из задержек гипервизора
 * (vCPU может быть не запущен на физическом ядре в нужный момент).
 *
 * Аргументы:
 *   --period-us P   период тика, мкс (по умолчанию 1000)
 *   --duration S    длительность, с (по умолчанию 60)
 *   --out FILE      CSV: tick (номер запланированного тика), lateness_ns,
 *                   missed (сколько следующих тиков пропущено),
 *                   t_wake_mono_ns (момент пробуждения по CLOCK_MONOTONIC —
 *                   те же часы, что у perf -k CLOCK_MONOTONIC, для привязки
 *                   опозданий к событиям KSM) — обязательный
 *   --fifo PRIO     SCHED_FIFO с приоритетом PRIO (по умолчанию SCHED_OTHER,
 *                   как у обычных серверов)
 *   --state-mib M   «состояние» тик-сервера: M МиБ анонимной памяти, страницы
 *                   которой повторяются (--state-distinct D вариантов,
 *                   по умолчанию 64), т.е. KSM может их слить (если KSM для
 *                   процесса включён снаружи, например ksm_launch);
 *   --touch-per-tick K  на каждом тике «обновить» K страниц состояния по кругу:
 *                   прочитать и записать обратно тот же байт. Содержимое не
 *                   меняется, поэтому KSM снова сливает страницу, а следующая
 *                   запись снова вызывает COW — устойчивый цикл «слил → COW»,
 *                   худший случай для задержки тика.
 * В CSV добавляется work_ns — длительность «работы» тика (обновление
 * состояния), аналог длительности тика игрового сервера.
 *
 * Массив результатов выделяется и «прогревается» заранее, а запись в файл
 * идёт только после окончания измерения, чтобы ввод-вывод не влиял на замер.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <getopt.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static int64_t ts_ns(const struct timespec *t)
{
    return (int64_t)t->tv_sec * 1000000000LL + t->tv_nsec;
}

static struct timespec ns_ts(int64_t ns)
{
    struct timespec t = {.tv_sec = ns / 1000000000LL, .tv_nsec = ns % 1000000000LL};
    return t;
}

int main(int argc, char **argv)
{
    long period_us = 1000;
    double duration = 60;
    const char *out = NULL;
    int fifo_prio = 0;
    long state_mib = 0, touch_per_tick = 0, state_distinct = 64;

    static struct option opts[] = {
        {"period-us", required_argument, 0, 'p'},
        {"duration", required_argument, 0, 'd'},
        {"out", required_argument, 0, 'o'},
        {"fifo", required_argument, 0, 'f'},
        {"state-mib", required_argument, 0, 's'},
        {"touch-per-tick", required_argument, 0, 'k'},
        {"state-distinct", required_argument, 0, 'D'},
        {0, 0, 0, 0}};
    int c;
    while ((c = getopt_long(argc, argv, "", opts, NULL)) != -1) {
        switch (c) {
        case 'p': period_us = atol(optarg); break;
        case 'd': duration = atof(optarg); break;
        case 'o': out = optarg; break;
        case 'f': fifo_prio = atoi(optarg); break;
        case 's': state_mib = atol(optarg); break;
        case 'k': touch_per_tick = atol(optarg); break;
        case 'D': state_distinct = atol(optarg) > 0 ? atol(optarg) : 1; break;
        default:
            fprintf(stderr, "Использование: %s --out FILE [--period-us P] [--duration S] [--fifo PRIO]\n", argv[0]);
            return 2;
        }
    }
    if (!out || period_us <= 0 || duration <= 0) {
        fprintf(stderr, "Нужны --out, положительные --period-us и --duration\n");
        return 2;
    }

    if (fifo_prio > 0) {
        struct sched_param sp = {.sched_priority = fifo_prio};
        if (sched_setscheduler(0, SCHED_FIFO, &sp) != 0) {
            fprintf(stderr, "SCHED_FIFO: %s\n", strerror(errno));
            return 1;
        }
    }

    /* Состояние: заполняется повторяющимися страницами (D вариантов). */
    long pg = sysconf(_SC_PAGESIZE);
    size_t state_pages = (size_t)state_mib * (1 << 20) / pg;
    volatile char *state = NULL;
    if (state_pages) {
        state = mmap(NULL, state_pages * pg, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (state == MAP_FAILED) { perror("mmap"); return 1; }
        for (size_t i = 0; i < state_pages; i++) {
            uint64_t *q = (uint64_t *)(state + i * pg);
            uint64_t v = (uint64_t)(i % state_distinct) + 1;
            for (long w2 = 0; w2 < pg / 8; w2++)
                q[w2] = v * 0x9E3779B97F4A7C15ULL ^ ((uint64_t)w2 * 0xBF58476D1CE4E5B9ULL);
        }
    }
    size_t cursor = 0;

    int64_t period_ns = (int64_t)period_us * 1000;
    size_t n = (size_t)(duration * 1e9 / period_ns);   /* число запланированных тиков */
    /* Массивы на худший случай (ни одного пропуска); прогрев memset-ом,
     * чтобы во время замера не было page fault при первой записи. */
    int64_t *tick = malloc(n * sizeof *tick);
    int64_t *late = malloc(n * sizeof *late);
    int64_t *missed = malloc(n * sizeof *missed);
    int64_t *twake = malloc(n * sizeof *twake);
    int64_t *work = malloc(n * sizeof *work);
    if (!tick || !late || !missed || !twake || !work) { perror("malloc"); return 1; }
    memset(tick, 0, n * sizeof *tick);
    memset(late, 0, n * sizeof *late);
    memset(missed, 0, n * sizeof *missed);
    memset(twake, 0, n * sizeof *twake);
    memset(work, 0, n * sizeof *work);

    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    int64_t t0 = ts_ns(&now) + period_ns;   /* первый тик через один период */

    size_t w = 0;                           /* число фактических пробуждений */
    int64_t k = 0;                          /* номер текущего запланированного тика */
    while ((size_t)k < n) {
        int64_t target = t0 + k * period_ns;
        struct timespec tt = ns_ts(target);
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &tt, NULL) == EINTR)
            ;
        clock_gettime(CLOCK_MONOTONIC, &now);
        int64_t t = ts_ns(&now);
        tick[w] = k;
        late[w] = t - target;
        twake[w] = t;
        /* «Работа» тика: обновить touch_per_tick страниц состояния. */
        if (state_pages && touch_per_tick) {
            for (long j = 0; j < touch_per_tick; j++) {
                volatile char *b = state + cursor * pg;
                *b = *b;                      /* тот же байт: COW без смены содержимого */
                cursor = (cursor + 1) % state_pages;
            }
            clock_gettime(CLOCK_MONOTONIC, &now);
            work[w] = ts_ns(&now) - t;
            t = ts_ns(&now);                  /* пропуски считать от конца работы */
        }
        /* Следующий тик — первый запланированный момент, который ещё не прошёл. */
        int64_t next = k + 1;
        while (t0 + next * period_ns < t)
            next++;
        missed[w] = next - k - 1;
        k = next;
        w++;
    }

    FILE *f = fopen(out, "w");
    if (!f) { perror(out); return 1; }
    fprintf(f, "tick,lateness_ns,missed,t_wake_mono_ns,work_ns\n");
    for (size_t i = 0; i < w; i++)
        fprintf(f, "%lld,%lld,%lld,%lld,%lld\n", (long long)tick[i], (long long)late[i],
                (long long)missed[i], (long long)twake[i], (long long)work[i]);
    fclose(f);
    free(tick); free(late); free(missed); free(twake); free(work);
    return 0;
}
