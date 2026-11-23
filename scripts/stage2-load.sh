#!/usr/bin/env bash
# =============================================================================
# stage2-load.sh — этап 2: задержка ответа под нагрузкой при KSM вкл./выкл.
#
# Один прогон = нагрузка W, N контейнеров (по умолчанию 8), KSM M (on/off), повтор.
#   1) reset_state; pages_to_scan=1000 (настройка fast, решение владельца);
#   2) N контейнеров W, KSM внутри: ksm_launch с KSM_LAUNCH_MERGE=1 (on) или 0
#      (off) — дерево процессов одинаковое (Р11); прогрев WARM_S;
#   3) трассировка (perf ksm:* + sched_switch ksmd, cow_ksm раз в 10 мс) —
#      ВСЕГДА, в обоих вариантах (на ВМ сравниваем при одном режиме трассы, Л5);
#      сэмплеры KSM (1 с) и памяти контейнеров (5 с);
#   4) на каждый контейнер свой генератор с частотой RATE[W] запросов/с:
#      HTTP — loadgen через nsenter в сетевое пространство контейнера (Р21);
#      PostgreSQL — pgbench --rate (select-only) из отдельного контейнера,
#      разделяющего сеть с сервером;
#   5) хронология (от старта генераторов): PRE_S базовая линия при run=0 →
#      run=1 → окно слияния MERGE_S → устойчивая фаза STEADY_S. Окна фиксированы
#      относительно включения KSM и одинаковы для on и off.
#   6) остановка, run=2, контейнеры удаляются.
# Частоты RATE[W] выбираются пилотом (stage2-load-pilot.sh) и передаются через
# переменные RATE_NGINX, RATE_PYTHON, RATE_NODE, RATE_JVM, RATE_POSTGRES.
# Фактор привязки к ЦП (PINS, шаг 5 — механизм хвоста):
#   none   без привязок (ksmd на всех vCPU);
#   split  ksmd — на последнем vCPU, приложение и генераторы — на остальных.
#   Ограничить контейнер через --cpuset-cpus в rootless-podman нельзя: контроллер
#   cpuset не делегирован пользовательскому systemd (проверено), а менять
#   системную конфигурацию не стали. Поэтому после старта все процессы
#   контейнеров привязываются `taskset -apc` по cgroup.procs; новые процессы и
#   потоки наследуют привязку. Привязка ksmd восстанавливается safety_init.
# Параметры: WORKLOADS, N (8), MODES ("on off"), REPS (10), WARM_S (30),
# PRE_S (30), MERGE_S (60), STEADY_S (90), CONNS (4), SERIES (stage2-load),
# TRACE (1).
# Прогрев под нагрузкой (этап 3, Р22: на ВМ JVM прогревала JIT уже во время
# базовой фазы): WARM_LOAD_S (0 — выкл., как на этапе 2) секунд тот же поток
# запросов RATE[W] до начала записи, только для нагрузок из
# WARM_LOAD_WORKLOADS (по умолчанию "jvm"). KSM в это время не работает
# (run=2 после reset_state). Журналы прогрева — warmup-lat-c*.csv.gz, в анализ
# не входят.
# Парный дизайн (этап 4, Р24): PAIRED=1 — в каждом повторе нагрузки идут в
# случайном порядке (SEED, по умолчанию 20261119), и для каждой нагрузки два
# прогона «вкл.» и «выкл.» стоят ПОДРЯД в случайном порядке внутри пары.
# Тогда разность внутри пары почти не содержит дрейфа (нагрев, фон, питание).
# Номер пары = номер повтора; в params.json — paired, pair_pos (1/2), seed.
# Без PAIRED порядок прежний: циклический сдвиг списка вариантов по повторам.
# REP_START — продолжить прерванную серию с повтора N (в новом каталоге серии;
# для другого порядка задайте и другой SEED).
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
source "$(dirname "$0")/lib/ksm_trace.sh"
export MIN_AVAIL_MIB="${MIN_AVAIL_MIB:-1200}"
safety_init

read -r -a WORKLOADS <<< "${WORKLOADS:-nginx python node jvm postgres}"
read -r -a MODES <<< "${MODES:-on off}"
read -r -a PINS <<< "${PINS:-none}"
NCPU=$(nproc); APP_CPUS_SPLIT="0-$((NCPU - 2))"
N="${N:-8}"; REPS="${REPS:-10}"; WARM_S="${WARM_S:-30}"
PRE_S="${PRE_S:-30}"; MERGE_S="${MERGE_S:-60}"; STEADY_S="${STEADY_S:-90}"
CONNS="${CONNS:-4}"; SERIES="${SERIES:-stage2-load}"; TRACE="${TRACE:-1}"
WARM_LOAD_S="${WARM_LOAD_S:-0}"; WARM_LOAD_WORKLOADS="${WARM_LOAD_WORKLOADS:-jvm}"
PAIRED="${PAIRED:-0}"; SEED="${SEED:-20261119}"
REP_START="${REP_START:-1}"   # продолжение прерванной серии: повторы REP_START..REPS (новый каталог)
if [[ "$PAIRED" == 1 ]]; then
    [[ "${MODES[*]}" == "on off" || "${MODES[*]}" == "off on" ]] || { echo "PAIRED=1 требует MODES=\"on off\"" >&2; exit 2; }
    RANDOM=$SEED      # воспроизводимый порядок
fi
DUR=$((PRE_S + MERGE_S + STEADY_S))
BIN="$REPO/workloads/ksm-probe/bin"; LG="$REPO/workloads/loadgen/bin/loadgen"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null
"$REPO/workloads/loadgen/build.sh"

wl_image() { case "$1" in nginx) echo docker.io/library/nginx:1.27-alpine ;; python) echo localhost/ksm-python:latest ;;
    node) echo localhost/ksm-node:latest ;; jvm) echo localhost/ksm-jvm:latest ;; postgres) echo docker.io/library/postgres:17-alpine ;; esac; }
wl_cmd() { case "$1" in nginx) echo '/docker-entrypoint.sh nginx -g daemon_off;' ;; python) echo 'gunicorn -w 2 -b 0.0.0.0:8000 app:app' ;;
    node) echo 'node server.js' ;; jvm) echo 'java -Dsun.net.httpserver.nodelay=true -Xms256m -Xmx256m -cp /app Server' ;; postgres) echo 'docker-entrypoint.sh postgres' ;; esac; }
wl_port() { case "$1" in nginx) echo 80 ;; postgres) echo 5432 ;; *) echo 8000 ;; esac; }
wl_rate() { local v="RATE_${1^^}"; echo "${!v:?не задана частота $v (см. stage2-load-pilot.sh)}"; }

OUT="$(new_series_dir "$SERIES")"
echo "Каталог серии: $OUT"
CONFIGS=(); for w in "${WORKLOADS[@]}"; do for m in "${MODES[@]}"; do for pn in "${PINS[@]}"; do
    CONFIGS+=("$w:$m:$pn"); done; done; done

for rep in $(seq -w "$REP_START" "$REPS"); do
    if [[ "$PAIRED" == 1 ]]; then
        # перемешать нагрузки (Фишер–Йетс), внутри пары — случайный порядок on/off
        ws=("${WORKLOADS[@]}")
        for ((i = ${#ws[@]} - 1; i > 0; i--)); do j=$((RANDOM % (i + 1))); t=${ws[i]}; ws[i]=${ws[j]}; ws[j]=$t; done
        order=()
        for w in "${ws[@]}"; do for pn in "${PINS[@]}"; do
            if (( RANDOM % 2 )); then order+=("$w:on:$pn" "$w:off:$pn"); else order+=("$w:off:$pn" "$w:on:$pn"); fi
        done; done
    else
        k=$(( (10#$rep - 1) % ${#CONFIGS[@]} ))
        order=("${CONFIGS[@]:k}" "${CONFIGS[@]:0:k}")
    fi
    declare -A seen_pair=()
    for cfg in "${order[@]}"; do
        IFS=: read -r w m pn <<< "$cfg"
        pair_pos=$(( ${seen_pair[$w:$pn]:-0} + 1 )); seen_pair[$w:$pn]=$pair_pos
        if [[ "$pn" == none ]]; then base="$OUT/$w-$m-rep$rep"; else base="$OUT/$w-$m-$pn-rep$rep"; fi
        mkdir -p "$base"
        reset_state > /dev/null
        ksm_set pages_to_scan 1000
        if [[ "$pn" == split ]]; then ksmd_set_affinity "$((NCPU - 1))"; app_cpus="$APP_CPUS_SPLIT"
        else ksmd_set_affinity "0-$((NCPU - 1))"; app_cpus="0-$((NCPU - 1))"; fi
        merge=0; [[ "$m" == on ]] && merge=1
        image="$(wl_image "$w")"; read -r -a cmd <<< "$(wl_cmd "$w")"
        cmd=("${cmd[@]//daemon_off;/daemon off;}")
        rate="$(wl_rate "$w")"; port="$(wl_port "$w")"
        env_pg=(); [[ "$w" == postgres ]] && env_pg=(-e POSTGRES_PASSWORD=ksm)
        ids=(); pids=(); pairs=()
        for i in $(seq 1 "$N"); do
            ids+=("$(podman run -d --label "$CTR_LABEL" --network=none -v "$BIN:/ksm:ro" \
                --entrypoint /ksm/ksm_launch -e KSM_LAUNCH_QUIET=1 -e KSM_LAUNCH_MERGE="$merge" \
                "${env_pg[@]}" "$image" "${cmd[@]}")")
        done
        sleep 3
        for id in "${ids[@]}"; do
            p=$(podman inspect --format '{{.State.Pid}}' "$id"); pids+=("$p"); pairs+=("${id:0:12}=$p")
            if [[ "$pn" == split ]]; then
                cg="/sys/fs/cgroup$(sed -n 's/^0:://p' /proc/$p/cgroup)"
                for q in $(cat "$cg/cgroup.procs"); do sudo taskset -apc "$app_cpus" "$q" > /dev/null; done
            fi
        done
        if [[ "$w" == postgres ]]; then
            # Готовность — по TCP: при первом старте образ поднимает ВРЕМЕННЫЙ сервер
            # только на сокете (initdb), потом перезапускает его; TCP есть только у
            # настоящего сервера. Затем данные pgbench (scale 5).
            for id in "${ids[@]}"; do
                for _ in $(seq 1 90); do podman exec "$id" pg_isready -q -h 127.0.0.1 -U postgres && break; sleep 1; done
                podman exec -e PGPASSWORD=ksm "$id" pgbench -i -q -s 5 -h 127.0.0.1 -U postgres postgres > "$base/pgbench-init-${id:0:12}.log" 2>&1
            done
        fi
        sleep "$WARM_S"
        # Прогрев под нагрузкой (только HTTP-нагрузки из WARM_LOAD_WORKLOADS).
        warm_load=0
        if (( WARM_LOAD_S > 0 )) && [[ " $WARM_LOAD_WORKLOADS " == *" $w "* && "$w" != postgres ]]; then
            warm_load="$WARM_LOAD_S"; wgens=()
            for i in "${!ids[@]}"; do
                sudo nsenter -t "${pids[$i]}" -n taskset -c "$app_cpus" "$LG" --port "$port" --rate "$rate" --conns "$CONNS" \
                    --duration "$WARM_LOAD_S" --out "$base/warmup-lat-c$i.csv" 2> "$base/warmup-loadgen-c$i.log" &
                wgens+=($!)
            done
            for g in "${wgens[@]}"; do wait "$g" || true; done
        fi
        # смещение «настенные часы − монотонные» (для перевода меток pgbench)
        rt_off=$(python3 -I -c 'import time;print(f"{time.clock_gettime(time.CLOCK_REALTIME)-time.clock_gettime(time.CLOCK_MONOTONIC):.6f}")')
        write_params "$base/params.json" experiment="$SERIES" workload="$w" mode="$m" ksm_launch_merge="$merge" \
            n="$N" rep="$rep" pin="$pn" ksmd_cpus="$(ksmd_get_affinity)" app_cpus="$app_cpus" \
            rate_per_ctr="$rate" conns="$CONNS" pre_s="$PRE_S" merge_s="$MERGE_S" \
            steady_s="$STEADY_S" warm_s="$WARM_S" warm_load_s="$warm_load" trace="$TRACE" realtime_minus_mono="$rt_off" \
            image="$image" image_digest="$(podman image inspect --format '{{.Digest}}' "$image")" \
            containers="${pairs[*]}" order="${order[*]}" paired="$PAIRED" pair_pos="$pair_pos" seed="$SEED"
        if [[ "$TRACE" == 1 ]]; then
            trace_start "$base/perf.data"
            python3 -I "$REPO/scripts/lib/vmstat_fast.py" --out "$base/vmstat.csv" --interval 0.01 --duration "$((DUR + 60))" &
            s_vm=$!
        fi
        python3 -I "$REPO/scripts/lib/ksm_sampler.py" --out "$base/ksm.csv" --interval 1 &
        s_ksm=$!
        sudo python3 -I "$REPO/scripts/lib/ctr_mem_sampler.py" --out "$base/mem.csv" --interval 5 "${pairs[@]}" &
        s_mem=$!
        # 4) генераторы
        gens=(); pgids=()
        for i in "${!ids[@]}"; do
            if [[ "$w" == postgres ]]; then
                pgids+=("$(podman run -d --label "$CTR_LABEL" --network "container:${ids[$i]}" -v "$base:/out" \
                    -e PGPASSWORD=ksm --entrypoint pgbench "$image" -h 127.0.0.1 -U postgres -n -S \
                    -c "$CONNS" -j 1 -R "$rate" -T "$DUR" -l --log-prefix "/out/pgbench-c$i" postgres)")
                if [[ "$pn" == split ]]; then      # taskset в образе нет — привязываем с хоста
                    pp=$(podman inspect --format '{{.State.Pid}}' "${pgids[-1]}")
                    cg="/sys/fs/cgroup$(sed -n 's/^0:://p' /proc/$pp/cgroup)"
                    for q in $(cat "$cg/cgroup.procs"); do sudo taskset -apc "$app_cpus" "$q" > /dev/null; done
                fi
            else
                sudo nsenter -t "${pids[$i]}" -n taskset -c "$app_cpus" "$LG" --port "$port" --rate "$rate" --conns "$CONNS" \
                    --duration "$DUR" --out "$base/lat-c$i.csv" 2> "$base/loadgen-c$i.log" &
                gens+=($!)
            fi
        done
        sleep "$PRE_S"
        sysw "$KSM/run" 1          # момент включения KSM виден в ksm.csv (столбец run)
        sleep $((MERGE_S + STEADY_S))
        # дождаться генераторов (HTTP: +10 с на хвосты внутри loadgen; pgbench: свой контейнер)
        for g in "${gens[@]}"; do wait "$g" || true; done
        for id in "${pgids[@]}"; do
            timeout 120 podman wait "$id" > /dev/null || true
            podman logs "$id" > "$base/pgbench-stdout-${id:0:12}.txt" 2>&1 || true
        done
        kill -TERM "$s_ksm"; wait "$s_ksm" || true
        sudo kill -TERM "$s_mem" 2>/dev/null || true; wait "$s_mem" 2>/dev/null || true
        if [[ "$TRACE" == 1 ]]; then
            kill -TERM "$s_vm" 2>/dev/null || true; wait "$s_vm" 2>/dev/null || true
            trace_stop
            trace_export "$base/perf.data" "$base/perf-script.txt.gz"
        fi
        sysw "$KSM/run" 2
        stop_our_containers
        sudo chown -R "$(id -u):$(id -g)" "$base"
        # Журналы каждого запроса — десятки МБ на прогон: сжать на месте. По схеме
        # хранения Р20 они, как и трассы, идут в архив + SHA-256, а не в git.
        find "$base" \( -name 'lat-c*.csv' -o -name 'warmup-lat-c*.csv' -o -regex '.*/pgbench-c[0-9]+\.[0-9]+' \) -exec nice -n 19 gzip -f {} +
        echo "rep$rep $w $m pin=$pn: $(ls "$base" | grep -cE '^(lat-c|pgbench-c)') журналов генераторов"
    done
done
echo "Серия завершена: $OUT"
