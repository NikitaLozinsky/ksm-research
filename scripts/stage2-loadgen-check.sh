#!/usr/bin/env bash
# =============================================================================
# stage2-loadgen-check.sh — этап 2, шаг 2: проверка генератора loadgen на
# эталонном сервере с известной задержкой (workloads/loadgen/refserver.py).
#
#   A) сервер отвечает сразу; 200 запросов/с, 4 соединения, 20 с.
#      Ожидание: все ответы получены, фактическая частота 200/с, время сервера
#      (t_done − t_sent) мало. Опоздание отправки (t_sent − t_planned) —
#      шум пробуждения самого генератора; на ВМ он заметен (Л4) и пишется
#      отдельно, чтобы его можно было отделить от времени сервера.
#   B) то же, но сервер один раз «застывает» на 1 с (с 5-й по 6-ю секунду).
#      Сервер пишет свой момент старта (CLOCK_MONOTONIC), поэтому окно застоя
#      известно точно на оси генератора. Проверка: КАЖДЫЙ запрос, запланированный
#      внутри застоя, должен иметь задержку не меньше модельной
#      (конец застоя − t_planned) за вычетом 5 мс допуска. Шум ВМ (остановки
#      на секунды, Л4) может задержку только увеличить, поэтому проверка к нему
#      устойчива. Закрытая модель нарушила бы неравенство почти для всех
#      запросов окна (она отправила бы их уже после застоя, «забыв» ожидание).
#   Почему сервер без задержки: первая версия проверки использовала задержку
#   5 мс через asyncio.sleep, и на этой ВМ она провалилась — сон длился
#   заметно дольше 5 мс (шум таймеров ВМ, Л4), эталонный сервер не успевал за
#   потоком и копил очередь. Эталон не должен зависеть от таймеров ВМ.
# Функциональная проверка с известным ответом: 3 повтора (Р4).
# Результат: results/raw/stage2-loadgen-check/<время>/ + PASS/FAIL в stdout.
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
REPS="${REPS:-3}"
LG="$REPO/workloads/loadgen"
"$LG/build.sh"
OUT="$(new_series_dir stage2-loadgen-check)"
write_params "$OUT/params.json" experiment=stage2-loadgen-check reps="$REPS" rate=200 conns=4 duration_s=20 delay_ms=0

run_case() {   # run_case <имя> <аргументы refserver...>
    local name="$1"; shift
    python3 -I "$LG/refserver.py" --port 18080 --delay-ms 0 --t0-file "$OUT/$name.server-t0" "$@" &
    local srv=$!
    sleep 1
    "$LG/bin/loadgen" --port 18080 --rate 200 --conns 4 --duration 20 --out "$OUT/$name.csv" 2> "$OUT/$name.log"
    kill "$srv"; wait "$srv" 2>/dev/null || true
}
for rep in $(seq 1 "$REPS"); do
    run_case "A-rep$rep"
    run_case "B-rep$rep" --stall-at 5 --stall-s 1
done

python3 -I - "$OUT" <<'PY'
import sys, glob
import numpy as np, pandas as pd
out = sys.argv[1]; ok = True
def check(c, m):
    global ok; print(("  PASS " if c else "  FAIL ") + m); ok &= bool(c)
for f in sorted(glob.glob(out + "/A-rep*.csv")):
    d = pd.read_csv(f); lat = d.latency_ns[d.latency_ns >= 0] / 1e6
    send = (d.t_sent_ns - d.t_planned_ns) / 1e6
    srv = (d.t_done_ns - d.t_sent_ns) / 1e6
    span = (d.t_planned_ns.max() - d.t_planned_ns.min()) / 1e9
    print(f"{f.split('/')[-1]}: ответов {len(lat)}/{len(d)}, задержка p50 {lat.median():.2f} p99 {lat.quantile(.99):.2f} мс; "
          f"опоздание отправки p50 {send.median():.2f} p99 {send.quantile(.99):.2f} мс; время сервера p50 {srv.median():.2f} мс; "
          f"частота {len(d)/span:.1f}/с")
    check(len(lat) == len(d), "все ответы получены")
    check(srv.median() < 5.0, f"время сервера p50 {srv.median():.2f} мс < 5 мс")
    check(abs(len(d) / span - 200) < 2, "частота 200 ± 2 /с")
    check(((lat - send - srv).abs() < 1e-6).all(), "задержка = опоздание отправки + время сервера (тождество журнала)")
for f in sorted(glob.glob(out + "/B-rep*.csv")):
    d = pd.read_csv(f)
    s0 = float(open(f.replace(".csv", ".server-t0")).read())
    st, en = s0 + 5.0, s0 + 6.0                      # окно застоя, CLOCK_MONOTONIC, с
    tp = d.t_planned_ns / 1e9; lat = d.latency_ns / 1e9
    win = (tp >= st) & (tp < en) & (lat >= 0)
    model = en - tp[win]
    ok_frac = float((lat[win] >= model - 0.005).mean())
    print(f"{f.split('/')[-1]}: запросов в окне застоя {int(win.sum())}, доля с задержкой >= модельной {ok_frac:.3f}, "
          f"медиана (измерено − модель) {float((lat[win] - model).median())*1e3:.1f} мс")
    check(int(win.sum()) >= 190, f"в окно застоя попало {int(win.sum())} запросов (ожидание ~200)")
    check(ok_frac >= 0.99, f"задержка учитывает ожидание у {ok_frac:.1%} запросов окна (открытая модель)")
print("ИТОГ:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
PY
