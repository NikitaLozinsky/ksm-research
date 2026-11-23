#!/usr/bin/env bash
# =============================================================================
# stage2-load-pilot.sh — этап 2, шаг 3: выбор рабочей частоты запросов.
#
# Для каждой нагрузки: N=8 контейнеров БЕЗ KSM (KSM_LAUNCH_MERGE=0, run=2),
# один прогрев, затем ступени частоты (запросов/с на контейнер) по STEP_S
# секунд; на каждой ступени все 8 генераторов работают одновременно.
# По результату (scripts/stage2-load-pilot-summary.py) выбирается частота
# ~30–50 % от насыщения: там задержку определяет не очередь, а сама система.
# Пилот: 1 повтор (оценочный замер, не результат).
# Параметры: WORKLOADS, N (8), STEP_S (20), CONNS (4), RATES_<W> (списки ступеней).
# JVM запускается с -Dsun.net.httpserver.nodelay=true: без TCP_NODELAY JDK HttpServer
# пишет заголовки и тело двумя сегментами, и Нейгл + отложенный ACK дают ~40 мс
# на ответ (найдено в первом пилоте этапа 2, docs/journal.md).
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
export MIN_AVAIL_MIB="${MIN_AVAIL_MIB:-1200}"
safety_init
read -r -a WORKLOADS <<< "${WORKLOADS:-nginx python node jvm postgres}"
N="${N:-8}"; STEP_S="${STEP_S:-20}"; CONNS="${CONNS:-4}"; WARM_S="${WARM_S:-30}"
RATES_nginx="${RATES_nginx:-50 100 200 400 800}"
RATES_python="${RATES_python:-25 50 100 200 400}"
RATES_node="${RATES_node:-50 100 200 400 800}"
RATES_jvm="${RATES_jvm:-50 100 200 400 800}"
RATES_postgres="${RATES_postgres:-25 50 100 200 400}"
BIN="$REPO/workloads/ksm-probe/bin"; LG="$REPO/workloads/loadgen/bin/loadgen"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null; "$REPO/workloads/loadgen/build.sh"
wl_image() { case "$1" in nginx) echo docker.io/library/nginx:1.27-alpine ;; python) echo localhost/ksm-python:latest ;;
    node) echo localhost/ksm-node:latest ;; jvm) echo localhost/ksm-jvm:latest ;; postgres) echo docker.io/library/postgres:17-alpine ;; esac; }
wl_cmd() { case "$1" in nginx) echo '/docker-entrypoint.sh nginx -g daemon_off;' ;; python) echo 'gunicorn -w 2 -b 0.0.0.0:8000 app:app' ;;
    node) echo 'node server.js' ;; jvm) echo 'java -Dsun.net.httpserver.nodelay=true -Xms256m -Xmx256m -cp /app Server' ;; postgres) echo 'docker-entrypoint.sh postgres' ;; esac; }
wl_port() { case "$1" in nginx) echo 80 ;; postgres) echo 5432 ;; *) echo 8000 ;; esac; }

OUT="$(new_series_dir stage2-load-pilot)"
echo "Каталог: $OUT"
for w in "${WORKLOADS[@]}"; do
    reset_state > /dev/null
    image="$(wl_image "$w")"; read -r -a cmd <<< "$(wl_cmd "$w")"; cmd=("${cmd[@]//daemon_off;/daemon off;}")
    env_pg=(); [[ "$w" == postgres ]] && env_pg=(-e POSTGRES_PASSWORD=ksm)
    ids=(); pids=()
    for i in $(seq 1 "$N"); do
        ids+=("$(podman run -d --label "$CTR_LABEL" --network=none -v "$BIN:/ksm:ro" --entrypoint /ksm/ksm_launch \
            -e KSM_LAUNCH_QUIET=1 -e KSM_LAUNCH_MERGE=0 "${env_pg[@]}" "$image" "${cmd[@]}")")
    done
    sleep 3
    for id in "${ids[@]}"; do pids+=("$(podman inspect --format '{{.State.Pid}}' "$id")"); done
    if [[ "$w" == postgres ]]; then
        for id in "${ids[@]}"; do
            for _ in $(seq 1 90); do podman exec "$id" pg_isready -q -h 127.0.0.1 -U postgres && break; sleep 1; done
            podman exec -e PGPASSWORD=ksm "$id" pgbench -i -q -s 5 -h 127.0.0.1 -U postgres postgres > /dev/null 2>&1
        done
    fi
    sleep "$WARM_S"
    rv="RATES_$w"
    for r in ${!rv}; do
        d="$OUT/$w-r$r"; mkdir -p "$d"
        write_params "$d/params.json" experiment=stage2-load-pilot workload="$w" n="$N" rate_per_ctr="$r" \
            conns="$CONNS" step_s="$STEP_S" \
            realtime_minus_mono="$(python3 -I -c 'import time;print(f"{time.clock_gettime(time.CLOCK_REALTIME)-time.clock_gettime(time.CLOCK_MONOTONIC):.6f}")')"
        gens=(); pgids=()
        for i in "${!ids[@]}"; do
            if [[ "$w" == postgres ]]; then
                pgids+=("$(podman run -d --label "$CTR_LABEL" --network "container:${ids[$i]}" -v "$d:/out" \
                    -e PGPASSWORD=ksm --entrypoint pgbench "$image" -h 127.0.0.1 -U postgres -n -S \
                    -c "$CONNS" -j 1 -R "$r" -T "$STEP_S" -l --log-prefix "/out/pgbench-c$i" postgres)")
            else
                sudo nsenter -t "${pids[$i]}" -n "$LG" --port "$(wl_port "$w")" --rate "$r" --conns "$CONNS" \
                    --duration "$STEP_S" --out "$d/lat-c$i.csv" 2> "$d/loadgen-c$i.log" &
                gens+=($!)
            fi
        done
        for g in "${gens[@]}"; do wait "$g" || true; done
        for id in "${pgids[@]}"; do timeout 120 podman wait "$id" > /dev/null || true
            podman logs "$id" > "$d/pgbench-${id:0:12}.out" 2>&1 || true; podman rm -f -v "$id" > /dev/null; done
        sudo chown -R "$(id -u):$(id -g)" "$d"
        echo "$w r=$r готово"
    done
    stop_our_containers
done
echo "Пилот завершён: $OUT"
