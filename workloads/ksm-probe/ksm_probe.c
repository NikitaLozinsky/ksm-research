/*
 * ksm_probe.c — тестовая нагрузка для проверки работы KSM.
 *
 * Программа выделяет N МиБ анонимной памяти (mmap) и заполняет её страницами,
 * у которых ровно K различных вариантов содержимого: страница номер i
 * получает вариант (i mod K). Поэтому заранее известно, сколько памяти KSM
 * может сэкономить в идеале:
 *     всего страниц P = N МиБ / 4 КиБ
 *     pages_shared  = K          (по одной общей копии на вариант)
 *     pages_sharing = P - K      (остальные ссылаются на общие копии)
 * (если P/K <= max_page_sharing; иначе KSM делает несколько копий варианта).
 * Это позволяет сравнить измерение с теорией.
 *
 * Режимы включения KSM (--mode):
 *   none     — ничего не делать (контроль: слияния быть не должно,
 *              если KSM не включён для процесса снаружи, например
 *              унаследованным prctl);
 *   madvise  — madvise(MADV_MERGEABLE) на выделенную область;
 *   prctl    — prctl(PR_SET_MEMORY_MERGE, 1) ДО выделения памяти:
 *              KSM включается для всего процесса.
 *
 * Дополнительно:
 *   --fork          выделение памяти делает дочерний процесс после fork()
 *                   (родитель только ставит режим и ждёт). Нужно для проверки
 *                   наследования prctl через fork.
 *   --fork-after-fill  родитель заполняет память и ПОТОМ делает fork; потомок
 *                   ничего не пишет и просто держит память. Страницы физически
 *                   общие (fork COW) ещё до KSM. Нужно, чтобы проверить, как
 *                   KSM учитывает такие страницы в pages_sharing (prefork-
 *                   серверы: gunicorn, nginx, postgres).
 *   --no-thp        madvise(MADV_NOHUGEPAGE): запретить huge-страницы для
 *                   области (по умолчанию действует системная настройка THP).
 *   --hold S        держать память S секунд, затем напечатать итог и выйти
 *                   (0 = держать до SIGTERM/SIGINT).
 *   --ready-file F  создать файл F, когда память заполнена (сигнал скрипту).
 *   --touch-after S через S секунд после заполнения записать по одному байту
 *                   в каждую страницу (один проход). Если страница была слита
 *                   KSM, запись вызывает copy-on-write (счётчик cow_ksm).
 *                   Нужно, чтобы измерить цену COW и проверить её учёт.
 *                   После прохода печатается строка JSON с его длительностью.
 *   SIGUSR1         то же, что --touch-after, но по сигналу: скрипт сам
 *                   выбирает момент (например, после остановки ksmd).
 *
 * В конце (или по сигналу) печатает одну строку JSON со статусом: PID,
 * PR_GET_MEMORY_MERGE, поля /proc/self/ksm_stat. Так скрипт получает
 * результат без разбора человекочитаемого текста.
 *
 * Сборка: workloads/ksm-probe/build.sh  (статическая, чтобы бинарник работал
 * в любом контейнере без библиотек).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <getopt.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* На случай старых заголовков: значения из include/uapi/linux/prctl.h. */
#ifndef PR_SET_MEMORY_MERGE
#define PR_SET_MEMORY_MERGE 67
#define PR_GET_MEMORY_MERGE 68
#endif

static volatile sig_atomic_t stop_flag = 0;
static volatile sig_atomic_t touch_req = 0;
static void on_signal(int sig) { (void)sig; stop_flag = 1; }
static void on_usr1(int sig) { (void)sig; touch_req = 1; }

/* Прочитать одно поле из /proc/self/ksm_stat. Числа возвращаются как есть,
 * "yes"/"no" превращаются в 1/0. -1 означает «поле не найдено». */
static long ksm_stat_field(const char *name)
{
    FILE *f = fopen("/proc/self/ksm_stat", "r");
    if (!f)
        return -1;
    char key[64], val[64];
    long result = -1;
    while (fscanf(f, "%63[^ :]%*[ :]%63s\n", key, val) == 2) {
        if (strcmp(key, name) == 0) {
            if (strcmp(val, "yes") == 0)      result = 1;
            else if (strcmp(val, "no") == 0)  result = 0;
            else                              result = atol(val);
            break;
        }
    }
    fclose(f);
    return result;
}

/* Печать итоговой строки JSON. role: "main" / "parent" / "child". */
static void report(const char *role, const char *mode, long pages, long distinct,
                   int pr_get_at_start)
{
    int pr_get_now = prctl(PR_GET_MEMORY_MERGE, 0, 0, 0, 0);
    printf("{\"role\":\"%s\",\"pid\":%d,\"ppid\":%d,\"mode\":\"%s\","
           "\"pages\":%ld,\"distinct\":%ld,"
           "\"pr_get_at_start\":%d,\"pr_get_now\":%d,"
           "\"ksm_merge_any\":%ld,\"ksm_mergeable\":%ld,"
           "\"ksm_merging_pages\":%ld,\"ksm_rmap_items\":%ld,"
           "\"ksm_process_profit\":%ld}\n",
           role, getpid(), getppid(), mode, pages, distinct,
           pr_get_at_start, pr_get_now,
           ksm_stat_field("ksm_merge_any"), ksm_stat_field("ksm_mergeable"),
           ksm_stat_field("ksm_merging_pages"), ksm_stat_field("ksm_rmap_items"),
           ksm_stat_field("ksm_process_profit"));
    fflush(stdout);
}

/* Выделить и заполнить память. Возвращает адрес или NULL при ошибке. */
static char *alloc_and_fill(size_t bytes, long distinct, int use_madvise, int no_thp)
{
    long page = sysconf(_SC_PAGESIZE);
    char *buf = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (buf == MAP_FAILED) {
        perror("mmap");
        return NULL;
    }
    if (no_thp && madvise(buf, bytes, MADV_NOHUGEPAGE) != 0)
        perror("madvise(MADV_NOHUGEPAGE)");
    if (use_madvise && madvise(buf, bytes, MADV_MERGEABLE) != 0) {
        perror("madvise(MADV_MERGEABLE)");
        return NULL;
    }
    /* Заполнение: каждая 8-байтовая ячейка страницы зависит от варианта и
     * своей позиции, поэтому страницы разных вариантов отличаются, а
     * страницы одного варианта совпадают побайтно. Нулевых страниц нет. */
    size_t npages = bytes / page;
    for (size_t i = 0; i < npages; i++) {
        uint64_t *p = (uint64_t *)(buf + i * page);
        uint64_t variant = (uint64_t)(i % distinct) + 1;
        for (size_t w = 0; w < (size_t)page / 8; w++)
            p[w] = variant * 0x9E3779B97F4A7C15ULL ^ (w * 0xBF58476D1CE4E5B9ULL);
    }
    return buf;
}

static double mono_now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

/* Один проход записи по одному байту в каждую страницу. Значение байта
 * меняется (инвертируется), так что страница становится уникальной. */
static void touch_pages(char *buf, size_t bytes)
{
    long page = sysconf(_SC_PAGESIZE);
    double t0 = mono_now();
    for (size_t off = 0; off < bytes; off += page)
        buf[off] = (char)~buf[off];
    double t1 = mono_now();
    printf("{\"event\":\"touch\",\"pid\":%d,\"t_start_mono\":%.6f,\"t_end_mono\":%.6f,"
           "\"duration_s\":%.6f,\"pages\":%zu}\n",
           getpid(), t0, t1, t1 - t0, bytes / page);
    fflush(stdout);
}

/* Ждать hold секунд (0 = до сигнала). Если touch_after > 0, через
 * touch_after секунд один раз выполнить touch_pages. */
static void hold_memory(double hold, double touch_after, char *buf, size_t bytes)
{
    double t0 = mono_now();
    int touched = 0;
    while (!stop_flag) {
        usleep(100000);
        double el = mono_now() - t0;
        if (touch_after > 0 && !touched && el >= touch_after) {
            touch_pages(buf, bytes);
            touched = 1;
        }
        if (touch_req) {
            touch_req = 0;
            touch_pages(buf, bytes);
        }
        if (hold > 0 && el >= hold)
            break;
    }
}

static void usage(const char *argv0)
{
    fprintf(stderr,
            "Использование: %s [--mode none|madvise|prctl] [--mib N] [--distinct K]\n"
            "               [--fork] [--no-thp] [--hold S] [--ready-file F] [--touch-after S]\n", argv0);
}

int main(int argc, char **argv)
{
    const char *mode = "none";
    long mib = 256, distinct = 1024;
    int do_fork = 0, no_thp = 0, fork_after_fill = 0;
    double hold = 0, touch_after = 0;
    const char *ready_file = NULL;

    static struct option opts[] = {
        {"mode", required_argument, 0, 'm'},
        {"mib", required_argument, 0, 'n'},
        {"distinct", required_argument, 0, 'k'},
        {"fork", no_argument, 0, 'f'},
        {"fork-after-fill", no_argument, 0, 'F'},
        {"no-thp", no_argument, 0, 't'},
        {"hold", required_argument, 0, 'h'},
        {"ready-file", required_argument, 0, 'r'},
        {"touch-after", required_argument, 0, 'w'},
        {0, 0, 0, 0}};
    int c;
    while ((c = getopt_long(argc, argv, "", opts, NULL)) != -1) {
        switch (c) {
        case 'm': mode = optarg; break;
        case 'n': mib = atol(optarg); break;
        case 'k': distinct = atol(optarg); break;
        case 'f': do_fork = 1; break;
        case 'F': fork_after_fill = 1; break;
        case 't': no_thp = 1; break;
        case 'h': hold = atof(optarg); break;
        case 'r': ready_file = optarg; break;
        case 'w': touch_after = atof(optarg); break;
        default: usage(argv[0]); return 2;
        }
    }
    if (strcmp(mode, "none") && strcmp(mode, "madvise") && strcmp(mode, "prctl")) {
        usage(argv[0]);
        return 2;
    }
    if (distinct < 1) distinct = 1;

    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);
    signal(SIGUSR1, on_usr1);

    /* Состояние PR_GET_MEMORY_MERGE при старте: 1 означает, что флаг
     * унаследован от родителя (через fork и/или exec). */
    int pr_get_at_start = prctl(PR_GET_MEMORY_MERGE, 0, 0, 0, 0);

    if (strcmp(mode, "prctl") == 0) {
        if (prctl(PR_SET_MEMORY_MERGE, 1, 0, 0, 0) != 0) {
            fprintf(stderr, "prctl(PR_SET_MEMORY_MERGE): %s\n", strerror(errno));
            return 1;
        }
    }

    size_t bytes = (size_t)mib << 20;
    long pages = (long)(bytes / sysconf(_SC_PAGESIZE));
    int use_madvise = strcmp(mode, "madvise") == 0;

    if (do_fork) {
        /* Родитель: только режим (выше) и ожидание ребёнка. */
        report("parent", mode, 0, distinct, pr_get_at_start);
        pid_t pid = fork();
        if (pid < 0) { perror("fork"); return 1; }
        if (pid == 0) {
            int child_pr = prctl(PR_GET_MEMORY_MERGE, 0, 0, 0, 0);
            char *buf = alloc_and_fill(bytes, distinct, use_madvise, no_thp);
            if (!buf) _exit(1);
            if (ready_file) { FILE *f = fopen(ready_file, "w"); if (f) fclose(f); }
            hold_memory(hold, touch_after, buf, bytes);
            report("child", mode, pages, distinct, child_pr);
            _exit(0);
        }
        int st;
        while (waitpid(pid, &st, 0) < 0 && errno == EINTR)
            if (stop_flag) kill(pid, SIGTERM);
        return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
    }

    char *buf = alloc_and_fill(bytes, distinct, use_madvise, no_thp);
    if (!buf)
        return 1;
    if (fork_after_fill) {
        pid_t c = fork();
        if (c < 0) { perror("fork"); return 1; }
        if (c == 0) {                       /* потомок: только держит память */
            hold_memory(hold, 0, buf, bytes);
            report("child", mode, pages, distinct, pr_get_at_start);
            _exit(0);
        }
        if (ready_file) { FILE *f = fopen(ready_file, "w"); if (f) fclose(f); }
        hold_memory(hold, touch_after, buf, bytes);
        kill(c, SIGTERM);
        int st; waitpid(c, &st, 0);
        report("parent", mode, pages, distinct, pr_get_at_start);
        return 0;
    }
    if (ready_file) { FILE *f = fopen(ready_file, "w"); if (f) fclose(f); }
    hold_memory(hold, touch_after, buf, bytes);
    report("main", mode, pages, distinct, pr_get_at_start);
    munmap(buf, bytes);
    return 0;
}
