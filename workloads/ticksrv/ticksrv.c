#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <time.h>

enum { PAGES = 4096, PAGE = 4096, HZ = 60 };
static volatile sig_atomic_t stop;

static void on_stop(int sig) {
    (void)sig;
    stop = 1;
}

static int64_t mono_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) < 0) {
        perror("clock_gettime");
        exit(1);
    }
    return t.tv_sec * INT64_C(1000000000) + t.tv_nsec;
}

int main(void) {
    const char *arg = getenv("TICK_WRITES");
    char *end;
    long writes = strtol(arg ? arg : "128", &end, 10);
    if (*end || writes < 1 || writes >= PAGES) {
        fputs("TICK_WRITES: ожидается число от 1 до 4095\n", stderr);
        return 2;
    }
    const char *cpu_arg = getenv("TICK_CPU_WORK");
    long cpu_work = strtol(cpu_arg ? cpu_arg : "0", &end, 10);
    if (*end || cpu_work < 0 || cpu_work > 16000000) {
        fputs("TICK_CPU_WORK: ожидается число от 0 до 16000000\n", stderr);
        return 2;
    }
    unsigned char *buf = mmap(NULL, PAGES * PAGE, PROT_READ | PROT_WRITE,
                              MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
    if (buf == MAP_FAILED ||
        madvise(buf, PAGES * PAGE, MADV_NOHUGEPAGE) < 0 ||
        prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME, buf, PAGES * PAGE, "ticksrv-world") < 0) {
        perror("подготовка мира");
        return 1;
    }
    // Страницы одинаковы между контейнерами и различны внутри снимка.
    memset(buf, 0x5a, PAGES * PAGE);
    for (uint32_t i = 0; i < PAGES; i++) {
        memcpy(buf + i * PAGE, &i, sizeof(i));
    }
    volatile unsigned char *world = buf;
    const char *path = getenv("TICK_CSV");
    FILE *log = fopen(path ? path : "/out/ticks.csv", "w");
    if (!log) {
        perror("журнал тиков");
        return 1;
    }
    struct sigaction sa = { .sa_handler = on_stop };
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    fputs("tick,start_ns,end_ns,engine_delta_s,realtime_ns\n", log);

    const int64_t second = INT64_C(1000000000);
    const int64_t origin = mono_ns();
    int64_t tick = 0, slot = 1;
    size_t page = 0;
    uint64_t state = 1;
    while (!stop) {
        int64_t when = origin + slot * second / HZ;
        struct timespec deadline = { .tv_sec = when / second, .tv_nsec = when % second };
        int rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, NULL);
        if (rc == EINTR && stop) break;
        if (rc != 0) {
            fprintf(stderr, "clock_nanosleep: %s\n", strerror(rc));
            return 1;
        }
        int64_t start = mono_ns();
        for (long i = 0; i < writes; i++) {
            size_t offset = page * PAGE + 64;
            // Мутация с возвратом значения оставляет страницу кандидатом на повторное слияние.
            world[offset] ^= 1;
            world[offset] ^= 1;
            page = (page + 1) % (PAGES - 1);
        }
        // Работа симуляции и число страниц записи настраиваются независимо.
        state ^= (uint64_t)tick;
        for (long i = 0; i < cpu_work; i++) {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
        }
        world[(PAGES - 1) * PAGE + 64] = (unsigned char)(tick ^ state);
        int64_t done = mono_ns();
        if (fprintf(log, "%" PRId64 ",%" PRId64 ",%" PRId64 ",,\n", ++tick, start, done) < 0 ||
            ((tick == 1 || tick % HZ == 0) && fflush(log) != 0)) {
            perror("запись тика");
            return 1;
        }
        // После опоздания переходим к ближайшему будущему слоту, без серии тиков вдогонку.
        int64_t next_slot = ((mono_ns() - origin + 1) * HZ + second - 1) / second;
        slot++;
        if (slot < next_slot) slot = next_slot;
    }
    return fclose(log) != 0;
}
