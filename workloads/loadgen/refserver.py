#!/usr/bin/env python3
"""
refserver.py — эталонный HTTP-сервер для проверки генератора loadgen.

Отвечает на каждый GET через фиксированную задержку --delay-ms (asyncio, без
блокировок между соединениями). С --stall-at T --stall-s S сервер один раз
«застывает»: ответы на запросы, пришедшие в окне [T, T+S) секунд от старта,
задерживаются до момента T+S. Это модель остановки сервера (GC, остановка
vCPU) для проверки, что loadgen считает задержку от расписания.
"""
import argparse
import asyncio
import time

BODY = b'{"ok":1}'
RESP = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(BODY)).encode() + b"\r\nConnection: keep-alive\r\n\r\n" + BODY)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--delay-ms", type=float, default=5.0)
    ap.add_argument("--stall-at", type=float, default=-1)
    ap.add_argument("--stall-s", type=float, default=0)
    ap.add_argument("--t0-file", help="записать сюда момент старта (CLOCK_MONOTONIC, с)")
    a = ap.parse_args()
    t0 = time.monotonic()            # в Linux это CLOCK_MONOTONIC — та же ось, что у loadgen
    if a.t0_file:
        with open(a.t0_file, "w") as f:
            f.write(f"{t0:.9f}\n")

    async def handle(reader, writer):
        while True:
            data = await reader.readuntil(b"\r\n\r\n") if True else None
            if not data:
                break
            el = time.monotonic() - t0
            await asyncio.sleep(a.delay_ms / 1000)
            if a.stall_at >= 0 and a.stall_at <= el < a.stall_at + a.stall_s:
                await asyncio.sleep(max(0.0, t0 + a.stall_at + a.stall_s - time.monotonic()))
            writer.write(RESP)
            await writer.drain()

    async def safe(reader, writer):
        try:
            await handle(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    srv = await asyncio.start_server(safe, "127.0.0.1", a.port)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
