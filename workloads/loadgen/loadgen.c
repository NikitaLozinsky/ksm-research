/*
 * loadgen.c — генератор HTTP-нагрузки с ОТКРЫТОЙ моделью (без coordinated
 * omission) и журналом каждого запроса.
 *
 * Открытая модель: запросы отправляются по заранее заданному расписанию с
 * постоянной частотой R запросов/с, независимо от того, как быстро отвечает
 * сервер. Задержка считается от ЗАПЛАНИРОВАННОГО момента отправки:
 *     latency = t_done - t_planned
 * Если сервер «застыл», запросы, которые должны были уйти за время застоя,
 * получают большую задержку — как у реальных клиентов. Закрытая модель
 * (следующий запрос только после ответа на предыдущий) такие задержки
 * теряет: это и есть «coordinated omission» (Gil Tene; так же устроен wrk2).
 *
 * Устройство: C соединений keep-alive (HTTP/1.1, без конвейеризации).
 * Соединение i отправляет свой k-й запрос в момент
 *     t_planned = t0 + (k*C + i) / R,
 * т.е. общий поток — ровно R запросов/с с равными интервалами. Если
 * соединение ещё ждёт предыдущий ответ, следующий запрос уходит сразу после
 * него, а его задержка всё равно считается от t_planned.
 * Ожидание — epoll_pwait2 с наносекундным таймаутом: обычный epoll_wait
 * округляет до миллисекунд и сам добавлял бы до 1 мс задержки.
 *
 * Ответ разбирается по Content-Length (chunked не поддерживается — наши
 * серверы его не используют; такой ответ считается ошибкой).
 * Если сервер прислал «Connection: close» (так делает gunicorn с sync
 * worker-ами), соединение закрывается после ответа, и следующий запрос идёт
 * по новому соединению — как у настоящего клиента. Без этого следующий
 * запрос мог уйти в уже закрывающееся соединение и считался бы ошибкой
 * (найдено в пилоте этапа 2).
 *
 * Использование:
 *   loadgen --port 8000 [--host 127.0.0.1] [--path /] --rate R --conns C
 *           --duration S --out file.csv
 * CSV: idx,conn,t_planned_ns,t_sent_ns,t_done_ns,status,latency_ns
 *   (времена — CLOCK_MONOTONIC, та же ось, что у perf -k CLOCK_MONOTONIC;
 *    неполученный ответ: t_done_ns=0, status=-1, latency_ns=-1).
 * В stderr — краткая сводка.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <getopt.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static int64_t now_ns(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000000000LL + t.tv_nsec;
}

struct conn {
    int fd;
    int busy;             /* ждёт ответа */
    int64_t k;            /* номер следующего запроса этого соединения */
    int64_t cur;          /* индекс текущего запроса в журнале */
    char buf[65536];
    size_t len;
};

static const char *host = "127.0.0.1";
static int port = 0;
static char req[512];
static size_t req_len;
static struct sockaddr_in addr;

static int open_conn(void)
{
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    if (connect(fd, (struct sockaddr *)&addr, sizeof addr) != 0) { close(fd); return -1; }
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    return fd;
}

/* Разбор накопленного ответа: 1 — полный (status заполнен), 0 — ещё не весь,
 * -1 — не разобрать. */
static int parse_response(struct conn *c, int *status, int *close_after)
{
    c->buf[c->len < sizeof c->buf ? c->len : sizeof c->buf - 1] = 0;
    char *hend = strstr(c->buf, "\r\n\r\n");
    if (!hend) return c->len >= sizeof c->buf - 1 ? -1 : 0;
    if (sscanf(c->buf, "HTTP/%*d.%*d %d", status) != 1) return -1;
    size_t hlen = (size_t)(hend - c->buf) + 4;
    long clen = -1;
    for (char *p = c->buf; p < hend; ) {
        char *eol = strstr(p, "\r\n");
        if (!eol) break;
        if (strncasecmp(p, "Content-Length:", 15) == 0) clen = atol(p + 15);
        if (strncasecmp(p, "Connection:", 11) == 0) {
            const char *v = p + 11;
            while (*v == ' ') v++;
            if (strncasecmp(v, "close", 5) == 0) *close_after = 1;
        }
        p = eol + 2;
    }
    if (clen < 0) return -1;
    if (c->len < hlen + (size_t)clen) return 0;
    return 1;
}

int main(int argc, char **argv)
{
    const char *path = "/", *out = NULL;
    double rate = 0, duration = 0;
    int nconn = 4;
    static struct option opts[] = {
        {"host", required_argument, 0, 'H'}, {"port", required_argument, 0, 'p'},
        {"path", required_argument, 0, 'P'}, {"rate", required_argument, 0, 'r'},
        {"conns", required_argument, 0, 'c'}, {"duration", required_argument, 0, 'd'},
        {"out", required_argument, 0, 'o'}, {0, 0, 0, 0}};
    int o;
    while ((o = getopt_long(argc, argv, "", opts, NULL)) != -1) {
        switch (o) {
        case 'H': host = optarg; break;
        case 'p': port = atoi(optarg); break;
        case 'P': path = optarg; break;
        case 'r': rate = atof(optarg); break;
        case 'c': nconn = atoi(optarg); break;
        case 'd': duration = atof(optarg); break;
        case 'o': out = optarg; break;
        default: return 2;
        }
    }
    if (!port || rate <= 0 || duration <= 0 || nconn < 1 || !out) {
        fprintf(stderr, "Использование: loadgen --port P --rate R --conns C --duration S --out F [--host H] [--path /]\n");
        return 2;
    }
    signal(SIGPIPE, SIG_IGN);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    inet_pton(AF_INET, host, &addr.sin_addr);
    req_len = snprintf(req, sizeof req,
                       "GET %s HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n", path);

    int64_t total = (int64_t)(rate * duration);
    int64_t per_conn = (total + nconn - 1) / nconn;
    int64_t *tp = calloc(total, 8), *ts = calloc(total, 8), *td = calloc(total, 8);
    int *st = calloc(total, sizeof(int)), *cn = calloc(total, sizeof(int));
    struct conn *cs = calloc(nconn, sizeof *cs);
    if (!tp || !ts || !td || !st || !cn || !cs) { perror("calloc"); return 1; }
    for (int64_t i = 0; i < total; i++) st[i] = -1;

    int ep = epoll_create1(0);
    for (int i = 0; i < nconn; i++) {
        cs[i].fd = open_conn();
        if (cs[i].fd < 0) { perror("connect"); return 1; }
        struct epoll_event ev = {.events = EPOLLIN, .data.u32 = (uint32_t)i};
        epoll_ctl(ep, EPOLL_CTL_ADD, cs[i].fd, &ev);
    }

    double period_ns = 1e9 / rate;
    int64_t t0 = now_ns() + 100000000LL;      /* старт через 100 мс */
    int64_t deadline = t0 + (int64_t)(duration * 1e9) + 10000000000LL;  /* +10 с на хвосты */
    int64_t done_cnt = 0, errors = 0;

#define PLANNED(i, k) (t0 + (int64_t)(((k) * nconn + (i)) * period_ns))
    for (;;) {
        int64_t now = now_ns();
        int64_t next_due = INT64_MAX;
        int active = 0;
        for (int i = 0; i < nconn; i++) {
            struct conn *c = &cs[i];
            if (c->k >= per_conn || c->k * nconn + i >= total) {
                if (c->busy) active = 1;
                continue;
            }
            active = 1;
            if (c->busy) continue;
            int64_t due = PLANNED(i, c->k);
            if (due <= now) {
                int64_t idx = c->k * nconn + i;
                if (c->fd < 0) {                         /* переподключение после ошибки */
                    c->fd = open_conn();
                    if (c->fd < 0) { st[idx] = -2; errors++; c->k++; continue; }
                    struct epoll_event ev = {.events = EPOLLIN, .data.u32 = (uint32_t)i};
                    epoll_ctl(ep, EPOLL_CTL_ADD, c->fd, &ev);
                }
                tp[idx] = due; cn[idx] = i;
                ts[idx] = now_ns();
                if (write(c->fd, req, req_len) != (ssize_t)req_len) {
                    st[idx] = -2; errors++; close(c->fd); c->fd = -1; c->k++; continue;
                }
                c->busy = 1; c->cur = idx; c->len = 0;
            } else if (due < next_due) {
                next_due = due;
            }
        }
        if (!active || now > deadline) break;

        struct epoll_event evs[64];
        struct timespec to = {0, 0};
        int64_t wait = next_due == INT64_MAX ? 50000000LL : next_due - now_ns();
        if (wait > 50000000LL) wait = 50000000LL;
        if (wait > 0) { to.tv_sec = wait / 1000000000LL; to.tv_nsec = wait % 1000000000LL; }
        int n = epoll_pwait2(ep, evs, 64, &to, NULL);
        for (int e = 0; e < n; e++) {
            struct conn *c = &cs[evs[e].data.u32];
            if (c->fd < 0) continue;
            ssize_t r = read(c->fd, c->buf + c->len, sizeof c->buf - 1 - c->len);
            if (r <= 0) {                               /* сервер закрыл соединение */
                epoll_ctl(ep, EPOLL_CTL_DEL, c->fd, NULL);
                close(c->fd); c->fd = -1;
                if (c->busy) { st[c->cur] = -2; errors++; c->busy = 0; c->k++; }
                continue;
            }
            c->len += (size_t)r;
            if (!c->busy) { c->len = 0; continue; }
            int status = 0, close_after = 0, pr = parse_response(c, &status, &close_after);
            if (pr == 0) continue;
            int64_t t = now_ns();
            if (pr < 0) { st[c->cur] = -3; errors++; }
            else { td[c->cur] = t; st[c->cur] = status; done_cnt++; }
            c->busy = 0; c->len = 0; c->k++;
            if (close_after || pr < 0) {               /* следующий запрос — по новому соединению */
                epoll_ctl(ep, EPOLL_CTL_DEL, c->fd, NULL);
                close(c->fd); c->fd = -1;
            }
        }
    }

    FILE *f = fopen(out, "w");
    if (!f) { perror(out); return 1; }
    fprintf(f, "idx,conn,t_planned_ns,t_sent_ns,t_done_ns,status,latency_ns\n");
    int64_t n_ok = 0;
    for (int64_t i = 0; i < total; i++) {
        int64_t lat = (td[i] && st[i] > 0) ? td[i] - tp[i] : -1;
        if (lat >= 0) n_ok++;
        fprintf(f, "%lld,%d,%lld,%lld,%lld,%d,%lld\n", (long long)i, cn[i], (long long)tp[i],
                (long long)ts[i], (long long)td[i], st[i], (long long)lat);
    }
    fclose(f);
    fprintf(stderr, "loadgen: запланировано %lld, получено ответов %lld (успешных %lld), ошибок %lld\n",
            (long long)total, (long long)done_cnt, (long long)n_ok, (long long)errors);
    return 0;
}
