#!/usr/bin/env bash
# =============================================================================
# stage1-workloads.sh — этап 1, шаг 7.2–7.3: экономия памяти и затраты ЦП KSM
# на реальных нагрузках в контейнерах podman.
#
# Конфигурация прогона = (нагрузка W, число одинаковых контейнеров N,
# настройка KSM C). Один прогон:
#   1) reset_state; применить настройку KSM C (пока run=2 — так требует
#      max_page_sharing, см. decisions Р13);
#   2) запустить N контейнеров W; KSM включается ВНУТРИ контейнера лаунчером
#      /ksm/ksm_launch (decisions Р11), контейнеры помечены меткой эксперимента;
#   3) прогрев WARM_S секунд (приложения загружают модули, строят справочники);
#   4) сэмплеры: системные счётчики KSM и ЦП ksmd (1 с), память контейнеров
#      (Pss_Anon, RssAnon, cgroup anon/current; 5 с, через sudo);
#   5) run=1 и наблюдение до схождения: сделано не меньше MIN_SCANS полных
#      проходов (full_scans) после включения KSM И pages_sharing за последние
#      STABLE_S секунд вырос не больше чем на max(1 %, 256 страниц); не раньше
#      MIN_S и не дольше MAX_S. (До исправления 2026-10-17 условия на число
#      проходов не было, и при медленном сканировании критерий срабатывал
#      во время паузы между «рывками» слияния — см. decisions Р17.)
#   6) остановка сэмплеров, run=2, остановка контейнеров.
#
# Нагрузки (WORKLOADS): nginx python node jvm postgres (описание:
# workloads/apps/README.md). Образы python/node/jvm собираются
# scripts/build-workloads.sh.
# Настройки KSM (KSM_CFGS):
#   default  pages_to_scan=100, sleep_millisecs=20 (по умолчанию ядра)
#   fast     pages_to_scan=1000, sleep_millisecs=20
#   adv200   advisor_mode=scan-time, цель 200 с (по умолчанию ядра)
#   adv30    advisor_mode=scan-time, цель 30 с
#   mps16    как fast + max_page_sharing=16
#   mps1024  как fast + max_page_sharing=1024
#
# Параметры (окружение): WORKLOADS, NS ("1 4 8"), KSM_CFGS ("default"),
# REPS (10), WARM_S (30), MIN_S (60), MAX_S (300), STABLE_S (60), MIN_SCANS (3),
# MIN_AVAIL_MIB (1500 — сторож памяти), SERIES (stage1-workloads).
# Порядок конфигураций внутри повтора циклически сдвигается (Р5).
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
export MIN_AVAIL_MIB="${MIN_AVAIL_MIB:-1500}"
safety_init

read -r -a WORKLOADS <<< "${WORKLOADS:-nginx python node jvm postgres}"
read -r -a NS <<< "${NS:-1 4 8}"
read -r -a KSM_CFGS <<< "${KSM_CFGS:-default}"
REPS="${REPS:-10}"
WARM_S="${WARM_S:-30}"; MIN_S="${MIN_S:-60}"; MAX_S="${MAX_S:-300}"; STABLE_S="${STABLE_S:-60}"
MIN_SCANS="${MIN_SCANS:-3}"
SERIES="${SERIES:-stage1-workloads}"
BIN="$REPO/workloads/ksm-probe/bin"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null

# Описание нагрузок: образ и команда (без entrypoint образа — его заменяет
# ksm_launch, поэтому исходный entrypoint передаётся первым словом команды).
wl_image() {
    case "$1" in
        nginx)    echo docker.io/library/nginx:1.27-alpine ;;
        python)   echo localhost/ksm-python:latest ;;
        node)     echo localhost/ksm-node:latest ;;
        jvm)      echo localhost/ksm-jvm:latest ;;
        postgres) echo docker.io/library/postgres:17-alpine ;;
    esac
}
wl_cmd() {
    case "$1" in
        nginx)    echo '/docker-entrypoint.sh nginx -g daemon_off;' ;;
        python)   echo 'gunicorn -w 2 -b 0.0.0.0:8000 app:app' ;;
        node)     echo 'node server.js' ;;
        jvm)      echo 'java -Xms256m -Xmx256m -cp /app Server' ;;
        postgres) echo 'docker-entrypoint.sh postgres' ;;
    esac
}
wl_env() { [[ "$1" == postgres ]] && echo "-e POSTGRES_PASSWORD=ksm" || true; }

apply_ksm_cfg() {
    case "$1" in
        default) ;;
        fast)    ksm_set pages_to_scan 1000 ;;
        adv200)  ksm_set advisor_mode scan-time ;;
        adv30)   ksm_set advisor_mode scan-time; ksm_set advisor_target_scan_time 30 ;;
        mps16)   ksm_set max_page_sharing 16; ksm_set pages_to_scan 1000 ;;
        mps1024) ksm_set max_page_sharing 1024; ksm_set pages_to_scan 1000 ;;
        *) echo "неизвестная настройка KSM: $1" >&2; return 2 ;;
    esac
}

# Проверка, что все образы есть локально (сеть во время серии не нужна).
for w in "${WORKLOADS[@]}"; do
    podman image exists "$(wl_image "$w")" || { echo "нет образа $(wl_image "$w") (см. scripts/build-workloads.sh)" >&2; exit 1; }
done

OUT="$(new_series_dir "$SERIES")"
echo "Каталог серии: $OUT"
CONFIGS=()
for w in "${WORKLOADS[@]}"; do for n in "${NS[@]}"; do for c in "${KSM_CFGS[@]}"; do
    CONFIGS+=("$w:$n:$c"); done; done; done

for rep in $(seq -w 1 "$REPS"); do
    k=$(( (10#$rep - 1) % ${#CONFIGS[@]} ))
    order=("${CONFIGS[@]:k}" "${CONFIGS[@]:0:k}")
    for cfg in "${order[@]}"; do
        IFS=: read -r w n c <<< "$cfg"
        base="$OUT/$w-n$n-$c-rep$rep"
        reset_state > /dev/null
        apply_ksm_cfg "$c"
        image="$(wl_image "$w")"
        read -r -a cmd <<< "$(wl_cmd "$w")"
        cmd=("${cmd[@]//daemon_off;/daemon off;}")
        # 2) N контейнеров
        ids=(); pairs=()
        for i in $(seq 1 "$n"); do
            id=$(podman run -d --label "$CTR_LABEL" --network=none \
                 -v "$BIN:/ksm:ro" --entrypoint /ksm/ksm_launch \
                 -e KSM_LAUNCH_QUIET=1 $(wl_env "$w") "$image" "${cmd[@]}")
            ids+=("$id")
        done
        sleep 2
        for id in "${ids[@]}"; do
            pid=$(podman inspect --format '{{.State.Pid}}' "$id")
            [[ "$pid" -gt 0 ]] || { echo "контейнер $id не запущен:" >&2; podman logs "$id" >&2 | tail -5; exit 1; }
            pairs+=("${id:0:12}=$pid")
        done
        sleep "$WARM_S"
        digest=$(podman image inspect --format '{{.Digest}}' "$image")
        write_params "$base.json" experiment="$SERIES" workload="$w" n="$n" ksm_cfg="$c" rep="$rep" \
            image="$image" image_digest="$digest" warm_s="$WARM_S" min_s="$MIN_S" max_s="$MAX_S" \
            stable_s="$STABLE_S" min_scans="$MIN_SCANS" containers="${pairs[*]}" order="${order[*]}"
        # 4) сэмплеры
        python3 -I "$REPO/scripts/lib/ksm_sampler.py" --out "$base.ksm.csv" --interval 1 &
        s_ksm=$!
        sudo python3 -I "$REPO/scripts/lib/ctr_mem_sampler.py" --out "$base.mem.csv" \
            --interval 5 "${pairs[@]}" &
        s_mem=$!
        sleep 3   # несколько отсчётов до включения KSM (базовая линия)
        # 5) KSM и ожидание схождения
        scans0=$(cat "$KSM/full_scans")
        sysw "$KSM/run" 1
        t0=$SECONDS; hist=()
        while :; do
            sleep 5
            el=$((SECONDS - t0))
            hist+=("$(cat "$KSM/pages_sharing")")
            (( el >= MAX_S )) && { conv=max; break; }
            (( el < MIN_S )) && continue
            m=$((STABLE_S / 5)); (( ${#hist[@]} > m )) || continue
            now=${hist[-1]}; old=${hist[-1-m]}
            d=$(( now > old ? now - old : old - now ))
            tol=$(( now / 100 > 256 ? now / 100 : 256 ))
            scans=$(( $(cat "$KSM/full_scans") - scans0 ))
            if (( now > 0 && d <= tol && scans >= MIN_SCANS )); then conv=stable; break; fi
        done
        echo "{\"converged\":\"$conv\",\"elapsed_s\":$el,\"pages_sharing\":${hist[-1]},\"full_scans\":$(( $(cat "$KSM/full_scans") - scans0 ))}" > "$base.end.json"
        # 6) остановка
        kill -TERM "$s_ksm"; wait "$s_ksm" || true
        sudo kill -TERM "$s_mem" 2>/dev/null || true; wait "$s_mem" 2>/dev/null || true
        sysw "$KSM/run" 2
        stop_our_containers
        echo "rep$rep $w n=$n $c: $conv за ${el} с, pages_sharing=${hist[-1]}"
    done
done
echo "Серия завершена: $OUT"
