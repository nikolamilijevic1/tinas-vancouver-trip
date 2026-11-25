from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any

from fast_flights import FlightQuery, Passengers, create_query
from primp import Client
from selectolax.lexbor import LexborHTMLParser

from .models import FlightLegOut, FlightOptionOut, LayoverOut

GOOGLE_FLIGHTS_URL = "https://www.google.com/travel/flights"
ULCC_AIRLINES = {"Flair Airlines", "Lynx Air", "Swoop"}


def _fmt_time(raw: list[int | None] | None) -> str:
    padded = [*(raw or []), None, None]
    return f"{padded[0] or 0:02d}:{padded[1] or 0:02d}"


def _fmt_date(raw: list[int] | None, fallback: str) -> str:
    if not raw or len(raw) < 3:
        return fallback
    return f"{raw[0]:04d}-{raw[1]:02d}-{raw[2]:02d}"


def _parse_hour(time_str: str) -> int:
    try:
        return int(time_str.split(":")[0])
    except Exception:
        return 12


def _fetch_html_ca(q) -> str:
    """Fetch Google Flights with gl=CA & curr=CAD so prices match Canadian Google Flights."""
    client = Client(
        impersonate="chrome_145",
        impersonate_os="macos",
        referer=True,
        cookie_store=True,
    )
    params = q.params()
    params["gl"] = "CA"
    resp = client.get(GOOGLE_FLIGHTS_URL, params=params)
    return resp.text


def build_flight_booking_url(
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str,
    include_carry_on: bool,
    nonstop_only: bool = False,
    outbound_stops: int | None = None,
    airline_code: str | None = None,
    dep_hour: int | None = None,
) -> str:
    """Build a direct Google Flights URL targeting this exact outbound flight & Canadian CAD pricing."""
    seg0_kwargs: dict[str, Any] = {
        "date": depart_date,
        "from_airport": origin,
        "to_airport": destination,
    }
    if nonstop_only or outbound_stops == 0:
        seg0_kwargs["max_stops"] = 0
    elif outbound_stops is not None:
        seg0_kwargs["max_stops"] = outbound_stops

    if airline_code and len(airline_code) == 2 and airline_code.isalnum():
        seg0_kwargs["airlines"] = [airline_code]

    if dep_hour is not None and 0 <= dep_hour <= 23:
        seg0_kwargs["earliest_departure_hour"] = dep_hour
        seg0_kwargs["latest_departure_hour"] = min(23, dep_hour + 1)

    seg1_kwargs: dict[str, Any] = {
        "date": return_date,
        "from_airport": destination,
        "to_airport": origin,
    }
    if nonstop_only:
        seg1_kwargs["max_stops"] = 0

    q = create_query(
        flights=[FlightQuery(**seg0_kwargs), FlightQuery(**seg1_kwargs)],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        currency="CAD",
        language="en",
        carry_on_bags=1 if include_carry_on else 0,
    )
    return q.url() + "&gl=CA"


def _parse_flight_row(
    row: list[Any],
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str,
    nights: int,
    nonstop_only: bool,
    is_nearby_airport: bool,
    carry_on_included: bool,
) -> FlightOptionOut | None:
    try:
        flight = row[0]
        price_block = row[1]
        if not price_block or not price_block[0] or len(price_block[0]) < 2:
            return None
        price_cad = price_block[0][1]
        if price_cad is None or price_cad <= 0:
            return None

        airlines: list[str] = list(flight[1] or [])
        raw_legs = flight[2] or []
        if not raw_legs:
            return None

        legs: list[FlightLegOut] = []
        for lg in raw_legs:
            meta = lg[22] if len(lg) > 22 and isinstance(lg[22], list) else None
            acode = str(meta[0]) if meta and len(meta) > 0 and meta[0] else str(flight[0] or "")
            fnum = str(meta[1]) if meta and len(meta) > 1 and meta[1] else ""
            aname = str(meta[3]) if meta and len(meta) > 3 and meta[3] else (airlines[0] if airlines else acode)
            legs.append(
                FlightLegOut(
                    airline_code=acode,
                    airline_name=aname,
                    flight_number=f"{acode} {fnum}".strip() or acode,
                    from_code=str(lg[3] or origin),
                    from_name=str(lg[4] or lg[3] or origin),
                    to_code=str(lg[6] or destination),
                    to_name=str(lg[5] or lg[6] or destination),
                    departure_date=_fmt_date(lg[20] if len(lg) > 20 else None, depart_date),
                    departure_time=_fmt_time(lg[8] if len(lg) > 8 else None),
                    arrival_date=_fmt_date(lg[21] if len(lg) > 21 else None, depart_date),
                    arrival_time=_fmt_time(lg[10] if len(lg) > 10 else None),
                    duration_minutes=int(lg[11] or 0),
                    aircraft=str(lg[17]) if len(lg) > 17 and lg[17] else None,
                )
            )

        layovers: list[LayoverOut] = []
        raw_layovers = flight[13] if len(flight) > 13 and isinstance(flight[13], list) else []
        for lo in raw_layovers:
            if not isinstance(lo, list) or len(lo) < 2:
                continue
            layovers.append(
                LayoverOut(
                    airport_code=str(lo[1] or ""),
                    airport_name=str(lo[4] or lo[1] or "") if len(lo) > 4 else str(lo[1] or ""),
                    city=str(lo[5] or lo[1] or "") if len(lo) > 5 else str(lo[1] or ""),
                    duration_minutes=int(lo[0] or 0),
                    overnight=bool(lo[3]) if len(lo) > 3 else False,
                )
            )

        if not layovers and len(legs) > 1:
            for idx in range(len(legs) - 1):
                a_leg, b_leg = legs[idx], legs[idx + 1]
                try:
                    t1 = datetime.strptime(f"{a_leg.arrival_date} {a_leg.arrival_time}", "%Y-%m-%d %H:%M")
                    t2 = datetime.strptime(f"{b_leg.departure_date} {b_leg.departure_time}", "%Y-%m-%d %H:%M")
                    wait = max(0, int((t2 - t1).total_seconds() // 60))
                    overnight = b_leg.departure_date != a_leg.arrival_date
                except Exception:
                    wait, overnight = 90, False
                layovers.append(
                    LayoverOut(
                        airport_code=a_leg.to_code,
                        airport_name=a_leg.to_name,
                        city=a_leg.to_code,
                        duration_minutes=wait,
                        overnight=overnight,
                    )
                )

        total_duration = int(flight[9] or sum(l.duration_minutes for l in legs))
        stops = max(0, len(legs) - 1)
        has_overnight = any(lo.overnight for lo in layovers)
        primary_airline = airlines[0] if airlines else legs[0].airline_name
        airline_code = legs[0].airline_code if len(set(l.airline_code for l in legs)) == 1 else ""
        dep_hour = _parse_hour(legs[0].departure_time)

        booking_url = build_flight_booking_url(
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            return_date=return_date,
            include_carry_on=carry_on_included,
            nonstop_only=nonstop_only,
            outbound_stops=stops,
            airline_code=airline_code or None,
            dep_hour=dep_hour,
        )

        sig = f"{origin}-{destination}-{depart_date}-{return_date}-{legs[0].flight_number}-{legs[0].departure_time}-{price_cad}-{stops}"
        opt_id = hashlib.md5(sig.encode()).hexdigest()[:12]

        return FlightOptionOut(
            id=opt_id,
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            return_date=return_date,
            nights=nights,
            price_cad=int(round(price_cad)),
            airlines=airlines or [primary_airline],
            primary_airline=primary_airline,
            airline_code=airline_code,
            stops=stops,
            total_duration_minutes=total_duration,
            legs=legs,
            layovers=layovers,
            has_overnight_layover=has_overnight,
            is_nearby_airport=is_nearby_airport,
            carry_on_included=carry_on_included,
            booking_url=booking_url,
        )
    except Exception:
        return None


def fetch_pair_live(
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str,
    nonstop_only: bool = False,
    include_carry_on: bool = True,
    is_nearby_airport: bool = False,
) -> dict[str, Any]:
    """Fetch live Google Flights results (CA / CAD) for one round-trip date pair."""
    q = create_query(
        flights=[
            FlightQuery(date=depart_date, from_airport=origin, to_airport=destination),
            FlightQuery(date=return_date, from_airport=destination, to_airport=origin),
        ],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        currency="CAD",
        language="en",
        max_stops=0 if nonstop_only else None,
        carry_on_bags=1 if include_carry_on else 0,
    )
    pair_url = q.url() + "&gl=CA"
    nights = (date.fromisoformat(return_date) - date.fromisoformat(depart_date)).days

    html = _fetch_html_ca(q)
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if not script:
        return {
            "origin": origin,
            "destination": destination,
            "depart_date": depart_date,
            "return_date": return_date,
            "nights": nights,
            "flights": [],
            "booking_url": pair_url,
        }

    data_str = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data_str.endswith("errorHasStatus: true"):
        return {
            "origin": origin,
            "destination": destination,
            "depart_date": depart_date,
            "return_date": return_date,
            "nights": nights,
            "flights": [],
            "booking_url": pair_url,
        }

    payload = json.loads(data_str)
    best_rows = payload[2][0] if len(payload) > 2 and payload[2] and payload[2][0] else []
    other_rows = payload[3][0] if len(payload) > 3 and payload[3] and payload[3][0] else []

    parsed: list[FlightOptionOut] = []
    seen: set[str] = set()
    for row in [*best_rows, *other_rows]:
        opt = _parse_flight_row(
            row,
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            return_date=return_date,
            nights=nights,
            nonstop_only=nonstop_only,
            is_nearby_airport=is_nearby_airport,
            carry_on_included=include_carry_on,
        )
        if opt and opt.id not in seen:
            seen.add(opt.id)
            parsed.append(opt)

    return {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date,
        "return_date": return_date,
        "nights": nights,
        "flights": [f.model_dump() for f in parsed],
        "booking_url": pair_url,
    }


def generate_candidate_pairs(
    depart_start: str,
    depart_end: str,
    return_start: str,
    return_end: str,
    min_nights: int,
    max_nights: int,
    max_pairs: int = 10,
) -> list[tuple[str, str]]:
    """Build well-distributed (depart, return) pairs respecting earliest/latest departure & return."""
    today = date.today()
    dep_min = max(date.fromisoformat(depart_start), today + timedelta(days=1))
    dep_max = max(date.fromisoformat(depart_end), dep_min)
    ret_min = max(date.fromisoformat(return_start), dep_min + timedelta(days=1))
    ret_max = max(date.fromisoformat(return_end), ret_min)

    eff_min = max(1, min(min_nights, max_nights))
    eff_max = max(eff_min, max_nights)

    # Collect all pairs within both windows AND stay-length range
    all_pairs: list[tuple[date, date]] = []
    relaxed_pairs: list[tuple[date, date]] = []
    cur_dep = dep_min
    while cur_dep <= dep_max:
        cur_ret = ret_min
        while cur_ret <= ret_max:
            nights = (cur_ret - cur_dep).days
            if nights >= 1:
                relaxed_pairs.append((cur_dep, cur_ret))
                if eff_min <= nights <= eff_max:
                    all_pairs.append((cur_dep, cur_ret))
            cur_ret += timedelta(days=1)
        cur_dep += timedelta(days=1)

    pool = all_pairs or relaxed_pairs
    if not pool:
        return [(dep_min.isoformat(), ret_max.isoformat())]
    if len(pool) <= max_pairs:
        return [(a.isoformat(), b.isoformat()) for a, b in pool]

    target_nights = (eff_min + eff_max) / 2.0
    by_dep: dict[date, list[tuple[date, date]]] = {}
    for a, b in pool:
        by_dep.setdefault(a, []).append((a, b))

    unique_deps = sorted(by_dep.keys())
    if len(unique_deps) <= max_pairs:
        chosen_deps = unique_deps
    else:
        step = (len(unique_deps) - 1) / (max_pairs - 1)
        chosen_deps = [unique_deps[round(i * step)] for i in range(max_pairs)]

    selected: list[tuple[date, date]] = []
    selected_set: set[tuple[date, date]] = set()
    # Pass 1: one balanced return per departure date, alternating earlier/later return dates for variety
    for idx, dep in enumerate(chosen_deps):
        cands = sorted(by_dep[dep], key=lambda p: (abs((p[1] - p[0]).days - target_nights), p[1]))
        pick = cands[0] if idx % 2 == 0 or len(cands) == 1 else cands[min(1, len(cands) - 1)]
        selected.append(pick)
        selected_set.add(pick)

    # Pass 2: fill remaining slots maximizing return-date coverage across return_start..return_end
    if len(selected) < max_pairs:
        used_returns = {p[1] for p in selected}
        rem = [p for p in pool if p not in selected_set]
        rem.sort(
            key=lambda p: (
                0 if p[1] not in used_returns else 1,
                abs((p[1] - p[0]).days - target_nights),
                p[0],
                p[1],
            )
        )
        for p in rem:
            if len(selected) >= max_pairs:
                break
            selected.append(p)
            selected_set.add(p)
            used_returns.add(p[1])

    selected.sort()
    return [(a.isoformat(), b.isoformat()) for a, b in selected]


def rank_flights(flights: list[FlightOptionOut]) -> list[FlightOptionOut]:
    """Quietly rank flights by price + practicality (without exposing robotic scores)."""
    if not flights:
        return []

    prices = sorted(f.price_cad for f in flights)
    min_p = prices[0]
    spread = max(150, prices[min(len(prices) - 1, int(len(prices) * 0.75))] - min_p)

    for f in flights:
        note = None
        penalty = 0

        if f.stops == 1:
            penalty += 12
        elif f.stops >= 2:
            penalty += 34

        penalty += min(28, max(0, f.total_duration_minutes - 350) // 20)

        for lo in f.layovers:
            if lo.overnight:
                penalty += 30
                note = f"Overnight layover in {lo.city or lo.airport_code}"
            elif lo.duration_minutes < 55:
                penalty += 15
                note = note or f"Tight {lo.duration_minutes}m connection in {lo.airport_code}"
            elif lo.duration_minutes > 270:
                penalty += 12
                note = note or f"{round(lo.duration_minutes / 60, 1)}h layover in {lo.city or lo.airport_code}"

        if f.legs and not note:
            arr_h = _parse_hour(f.legs[-1].arrival_time)
            dep_h = _parse_hour(f.legs[0].departure_time)
            if 1 <= arr_h <= 4:
                penalty += 8
                note = f"Arrives late ({f.legs[-1].arrival_time})"
            elif dep_h < 6:
                penalty += 8
                note = f"Early departure ({f.legs[0].departure_time})"

        if f.primary_airline in ULCC_AIRLINES and not f.carry_on_included:
            note = note or "Carry-on bag fee extra"
        if f.is_nearby_airport:
            penalty += 14
            if f.origin != "YOW":
                note = f"Departs Montreal ({f.origin})"
            elif f.destination != "YVR":
                note = f"Lands Abbotsford ({f.destination})"

        price_part = max(0, 100 - int(((f.price_cad - min_p) / spread) * 65))
        comfort_part = max(0, 100 - penalty)
        f.rank_score = int(price_part * 0.62 + comfort_part * 0.38)
        f.note = note

    flights.sort(key=lambda x: (-x.rank_score, x.price_cad, x.total_duration_minutes))
    return flights
