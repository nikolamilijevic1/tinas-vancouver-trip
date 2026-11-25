from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

from . import database
from .flight_engine import fetch_pair_live, generate_candidate_pairs, rank_flights
from .models import FlightOptionOut
from .notifier import build_deal_email, is_email_configured, send_email

_executor = ThreadPoolExecutor(max_workers=5)
_watcher_task: asyncio.Task | None = None
_stop_event = asyncio.Event()


async def fetch_pair_cached(
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str,
    nonstop_only: bool,
    include_carry_on: bool,
    is_nearby_airport: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    cache_key = f"v2:{origin}:{destination}:{depart_date}:{return_date}:{int(nonstop_only)}:{int(include_carry_on)}"
    if not force_refresh:
        cached = await database.get_cached_pair(cache_key, ttl_seconds=600)
        if cached is not None:
            return cached

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        _executor,
        lambda: fetch_pair_live(
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            return_date=return_date,
            nonstop_only=nonstop_only,
            include_carry_on=include_carry_on,
            is_nearby_airport=is_nearby_airport,
        ),
    )
    if result.get("flights"):
        await database.set_cached_pair(cache_key, result)
    return result


async def run_alert_check(*, force_send: bool = False) -> dict[str, Any]:
    cfg = await database.get_email_alert()
    if not cfg.get("email"):
        return {"ok": False, "detail": "No email address saved."}

    pairs = generate_candidate_pairs(
        cfg["window_start"],
        cfg["window_end"],
        cfg["min_nights"],
        cfg["max_nights"],
        max_pairs=8,
    )
    sem = asyncio.Semaphore(4)

    async def worker(dep: str, ret: str):
        async with sem:
            return await fetch_pair_cached(
                origin=cfg["origin"],
                destination=cfg["destination"],
                depart_date=dep,
                return_date=ret,
                nonstop_only=cfg["nonstop_only"],
                include_carry_on=cfg["include_carry_on"],
                force_refresh=True,
            )

    results = await asyncio.gather(*(worker(d, r) for d, r in pairs), return_exceptions=True)
    all_flights: list[FlightOptionOut] = []
    for r in results:
        if isinstance(r, dict):
            for fd in r.get("flights", []):
                try:
                    all_flights.append(FlightOptionOut(**fd))
                except Exception:
                    pass

    ranked = rank_flights(all_flights)
    now_iso = datetime.now().isoformat(timespec="seconds")
    await database.save_email_alert({"last_checked_at": now_iso})

    if not ranked:
        return {"ok": True, "checked_at": now_iso, "sent": False, "detail": "No flights found."}

    cheapest = min(ranked, key=lambda x: x.price_cad)
    target = cfg.get("max_price_cad") or 850
    last_emailed_at = cfg.get("last_emailed_at")
    last_emailed_price = cfg.get("last_emailed_price")
    emailed_today = bool(last_emailed_at and last_emailed_at.startswith(date.today().isoformat()))

    reason = None
    if force_send:
        reason = f"Current best Ottawa → Vancouver flights in your window (lowest: CA${cheapest.price_cad})."
    elif cheapest.price_cad <= target and (not emailed_today or (last_emailed_price and cheapest.price_cad <= last_emailed_price - 25)):
        reason = f"Good deal found: CA${cheapest.price_cad} (under your CA${target} target)."
    elif cfg.get("daily_summary") and not emailed_today and datetime.now().hour >= 9:
        reason = f"Daily update: lowest fare right now is CA${cheapest.price_cad}."

    if reason:
        # Sort top picks: cheapest + best nonstop
        picks = [cheapest.model_dump()]
        if ranked[0].id != cheapest.id:
            picks.append(ranked[0].model_dump())
        for f in ranked:
            if len(picks) >= 4:
                break
            if all(p["id"] != f.id for p in picks):
                picks.append(f.model_dump())

        subject, text_body, html_body = build_deal_email(
            flights=picks,
            window_start=cfg["window_start"],
            window_end=cfg["window_end"],
            reason=reason,
        )
        ok, detail = await send_email(cfg["email"], subject, text_body, html_body)
        if ok:
            await database.save_email_alert(
                {"last_emailed_at": now_iso, "last_emailed_price": cheapest.price_cad}
            )
        return {"ok": ok, "checked_at": now_iso, "sent": ok, "lowest_cad": cheapest.price_cad, "detail": detail}

    return {
        "ok": True,
        "checked_at": now_iso,
        "sent": False,
        "lowest_cad": cheapest.price_cad,
        "detail": f"Checked — lowest is CA${cheapest.price_cad} (target is CA${target}).",
    }


async def _background_loop() -> None:
    while not _stop_event.is_set():
        try:
            cfg = await database.get_email_alert()
            if cfg.get("enabled") and cfg.get("email") and is_email_configured():
                last = cfg.get("last_checked_at")
                due = True
                if last:
                    try:
                        due = datetime.now() - datetime.fromisoformat(last) >= timedelta(hours=3)
                    except Exception:
                        due = True
                if due:
                    await run_alert_check(force_send=False)
        except Exception:
            pass
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=120)
        except asyncio.TimeoutError:
            pass


def start_watcher() -> None:
    global _watcher_task
    _stop_event.clear()
    if _watcher_task is None or _watcher_task.done():
        _watcher_task = asyncio.create_task(_background_loop())


async def stop_watcher() -> None:
    global _watcher_task
    _stop_event.set()
    if _watcher_task and not _watcher_task.done():
        _watcher_task.cancel()
        try:
            await _watcher_task
        except asyncio.CancelledError:
            pass
    _watcher_task = None
