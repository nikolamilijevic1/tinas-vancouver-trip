from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import database, watcher
from .flight_engine import generate_candidate_pairs, rank_flights
from .models import EmailAlertIn, FlightOptionOut, MatrixCellOut, PairSearchRequest, SearchRequest
from .notifier import is_email_configured

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await database.init_db()
    watcher.start_watcher()
    try:
        yield
    finally:
        await watcher.stop_watcher()


app = FastAPI(title="Tina's Vancouver Trip", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/tinas-vancouver-trip", status_code=302)


@app.get("/tinas-vancouver-trip")
async def tinas_trip_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "email_configured": is_email_configured()}


@app.post("/api/search/stream")
async def search_stream(req: SearchRequest):
    async def event_stream() -> AsyncGenerator[str, None]:
        origin_list = [o.strip().upper() for o in (req.origins or [req.origin]) if o.strip()]
        if not origin_list:
            origin_list = ["YOW"]

        dest_list = [d.strip().upper() for d in (req.destinations or [req.destination]) if d.strip()]
        if not dest_list:
            dest_list = ["YVR"]

        route_count = len(origin_list) * len(dest_list)
        # Keep total Google Flights queries snappy when comparing multiple origin/destination airports
        if route_count == 1:
            effective_max_pairs = req.max_pairs
        elif route_count == 2:
            effective_max_pairs = 6
        elif route_count <= 4:
            effective_max_pairs = 4
        else:
            effective_max_pairs = 3

        pairs = generate_candidate_pairs(
            req.depart_start,
            req.depart_end,
            req.return_start,
            req.return_end,
            req.min_nights,
            req.max_nights,
            max_pairs=effective_max_pairs,
        )

        jobs: list[dict[str, Any]] = []
        for orig in origin_list:
            for dest in dest_list:
                for dep, ret in pairs:
                    jobs.append(
                        {
                            "origin": orig,
                            "destination": dest,
                            "depart_date": dep,
                            "return_date": ret,
                            "is_nearby": (orig != "YOW" or dest != "YVR"),
                        }
                    )

        total = len(jobs)
        yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

        sem = asyncio.Semaphore(5)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def run_job(job: dict[str, Any]) -> None:
            async with sem:
                try:
                    res = await watcher.fetch_pair_cached(
                        origin=job["origin"],
                        destination=job["destination"],
                        depart_date=job["depart_date"],
                        return_date=job["return_date"],
                        nonstop_only=req.nonstop_only,
                        include_carry_on=req.include_carry_on,
                        is_nearby_airport=job["is_nearby"],
                        force_refresh=req.force_refresh,
                    )
                    await queue.put({"job": job, "res": res})
                except Exception as exc:
                    await queue.put({"job": job, "res": None, "error": str(exc)})

        tasks = [asyncio.create_task(run_job(j)) for j in jobs]
        all_flights: list[FlightOptionOut] = []
        matrix: list[MatrixCellOut] = []
        done = 0

        try:
            while done < total:
                item = await queue.get()
                done += 1
                job = item["job"]
                res = item.get("res")

                cell_out = None
                if res:
                    pair_flights: list[FlightOptionOut] = []
                    for fd in res.get("flights", []):
                        try:
                            opt = FlightOptionOut(**fd)
                            pair_flights.append(opt)
                            all_flights.append(opt)
                        except Exception:
                            pass

                    if pair_flights:
                        cheapest = min(pair_flights, key=lambda x: x.price_cad)
                        ns = [x for x in pair_flights if x.stops == 0]
                        cell = MatrixCellOut(
                            origin=job["origin"],
                            destination=job["destination"],
                            depart_date=job["depart_date"],
                            return_date=job["return_date"],
                            nights=res.get("nights", 0),
                            cheapest_cad=cheapest.price_cad,
                            cheapest_nonstop_cad=min(x.price_cad for x in ns) if ns else None,
                            booking_url=res.get("booking_url"),
                        )
                        matrix.append(cell)
                        cell_out = cell.model_dump()

                yield f"data: {json.dumps({'type': 'progress', 'completed': done, 'total': total, 'cell': cell_out})}\n\n"
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        dedup: dict[str, FlightOptionOut] = {}
        for f in all_flights:
            if f.id not in dedup or f.price_cad < dedup[f.id].price_cad:
                dedup[f.id] = f

        ranked = rank_flights(list(dedup.values()))
        matrix.sort(key=lambda c: (c.origin != req.origin.upper(), c.depart_date, c.return_date))

        yield f"data: {json.dumps({'type': 'complete', 'searched_at': datetime.now().isoformat(timespec='seconds'), 'matrix': [c.model_dump() for c in matrix], 'flights': [f.model_dump() for f in ranked[:80]]})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/search/pair")
async def search_pair(req: PairSearchRequest):
    try:
        res = await watcher.fetch_pair_cached(
            origin=req.origin.upper(),
            destination=req.destination.upper(),
            depart_date=req.depart_date,
            return_date=req.return_date,
            nonstop_only=req.nonstop_only,
            include_carry_on=req.include_carry_on,
            force_refresh=req.force_refresh,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    flights = rank_flights([FlightOptionOut(**f) for f in res.get("flights", [])])
    return {"flights": [f.model_dump() for f in flights], "booking_url": res.get("booking_url")}


@app.get("/api/alert")
async def get_alert():
    cfg = await database.get_email_alert()
    cfg["email_configured"] = is_email_configured()
    return cfg


@app.put("/api/alert")
async def save_alert(body: EmailAlertIn):
    saved = await database.save_email_alert(body.model_dump())
    saved["email_configured"] = is_email_configured()
    return saved


@app.post("/api/alert/send-now")
async def send_alert_now():
    return await watcher.run_alert_check(force_send=True)
