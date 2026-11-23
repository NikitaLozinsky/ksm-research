/*
 * ksm_launch.c — включает KSM для процесса и запускает указанную программу.
 *
 *     ksm_launch <программа> [аргументы...]
 *
 * Делает prctl(PR_SET_MEMORY_MERGE, 1) и затем execvp(программа). Если ядро
 * сохраняет флаг через exec (это и проверяется на этапе 0), то запущенная
 * программа и все её потомки получают KSM для всей анонимной памяти, без
 * изменения их исходного кода. Так же устроена опция MemoryKSM= в systemd.
 *
 * Применение:
 *   - снаружи: ksm_launch podman run ...   (флаг должен пройти через
 *     podman → conmon → crun → процесс контейнера);
 *   - внутри контейнера: как ENTRYPOINT перед настоящей программой.
 *
 * Переменные окружения:
 *   KSM_LAUNCH_QUIET=1  отключить диагностическую строку;
 *   KSM_LAUNCH_MERGE=0  режим «KSM выключен»: вместо включения флаг явно
 *                       СБРАСЫВАЕТСЯ (prctl(PR_SET_MEMORY_MERGE, 0)), затем тот
 *                       же exec. Нужен для контрольной конфигурации: дерево
 *                       процессов то же, отличается только флаг. Сброс (а не
 *                       просто «ничего не делать») гарантирует, что флаг не
 *                       пришёл по наследству от родителя.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <unistd.h>

#ifndef PR_SET_MEMORY_MERGE
#define PR_SET_MEMORY_MERGE 67
#define PR_GET_MEMORY_MERGE 68
#endif

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "Использование: %s <программа> [аргументы...]\n", argv[0]);
        return 2;
    }
    const char *merge_env = getenv("KSM_LAUNCH_MERGE");
    int merge = !(merge_env && strcmp(merge_env, "0") == 0);
    if (prctl(PR_SET_MEMORY_MERGE, merge, 0, 0, 0) != 0) {
        fprintf(stderr, "ksm_launch: prctl(PR_SET_MEMORY_MERGE, %d) не удался: %s\n",
                merge, strerror(errno));
        return 1;
    }
    if (!getenv("KSM_LAUNCH_QUIET"))
        fprintf(stderr, "ksm_launch: pid=%d PR_GET_MEMORY_MERGE=%d, exec %s\n",
                getpid(), prctl(PR_GET_MEMORY_MERGE, 0, 0, 0, 0), argv[1]);
    execvp(argv[1], argv + 1);
    fprintf(stderr, "ksm_launch: exec %s: %s\n", argv[1], strerror(errno));
    return 127;
}
