"""
Event Management System Data Store — PostgreSQL backend.
Tables: ems_venues, ems_bookings, ems_service_items, ems_booking_services
"""
from __future__ import annotations
import logging, uuid
from datetime import datetime
from typing import List, Optional
from db import get_conn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ems_venues (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    name            TEXT NOT NULL,
    capacity        INT  NOT NULL DEFAULT 0,
    hourly_rate     NUMERIC(12,2) NOT NULL DEFAULT 0,
    layout_options  TEXT NOT NULL DEFAULT '',  -- CSV: banquet,theatre,classroom
    description     TEXT NOT NULL DEFAULT '',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ems_venues_company ON ems_venues(company_id);

CREATE TABLE IF NOT EXISTS ems_bookings (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    venue_id        TEXT NOT NULL,
    client_id       TEXT NOT NULL DEFAULT '',
    client_name     TEXT NOT NULL,
    client_phone    TEXT NOT NULL DEFAULT '',
    event_name      TEXT NOT NULL,
    setup_start     TIMESTAMP NOT NULL,
    event_start     TIMESTAMP NOT NULL,
    event_end       TIMESTAMP NOT NULL,
    teardown_end    TIMESTAMP NOT NULL,
    status          TEXT NOT NULL DEFAULT 'tentative',  -- tentative|confirmed|cancelled
    hall_rent       NUMERIC(12,2) NOT NULL DEFAULT 0,
    services_total  NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ems_bookings_company ON ems_bookings(company_id);
CREATE INDEX IF NOT EXISTS idx_ems_bookings_venue   ON ems_bookings(venue_id, event_start, teardown_end);

CREATE TABLE IF NOT EXISTS ems_service_items (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'other',  -- food|av|seating|decoration|other
    unit         TEXT NOT NULL DEFAULT 'item',
    unit_price   NUMERIC(12,2) NOT NULL DEFAULT 0,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ems_services_company ON ems_service_items(company_id);

CREATE TABLE IF NOT EXISTS ems_booking_services (
    id          TEXT PRIMARY KEY,
    booking_id  TEXT NOT NULL,
    service_id  TEXT NOT NULL,
    service_name TEXT NOT NULL,
    quantity    NUMERIC(10,2) NOT NULL DEFAULT 1,
    unit_price  NUMERIC(12,2) NOT NULL DEFAULT 0,
    subtotal    NUMERIC(12,2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ems_bk_services ON ems_booking_services(booking_id);

CREATE TABLE IF NOT EXISTS ems_clients (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    organization TEXT NOT NULL DEFAULT '',
    phone        TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    tin          TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ems_clients_company ON ems_clients(company_id);

CREATE TABLE IF NOT EXISTS ems_visitors (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    phone        TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    purpose      TEXT NOT NULL DEFAULT '',
    host         TEXT NOT NULL DEFAULT '',
    checkin_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    checkout_at  TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ems_visitors_company ON ems_visitors(company_id, checkin_at);

CREATE TABLE IF NOT EXISTS ems_appointments (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    visitor_name TEXT NOT NULL,
    phone        TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    host         TEXT NOT NULL DEFAULT '',
    scheduled_at TIMESTAMP NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|cancelled|completed
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ems_appointments_company ON ems_appointments(company_id, scheduled_at);
"""


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("ems schema ready")
    except Exception as e:
        logger.error("ems schema init failed: %s", e)


class EMSDataStore:

    def ensure_schema(self):
        ensure_schema()

    # Venues
    def get_venues(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM ems_venues WHERE company_id=%s AND is_active ORDER BY name", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_venues: %s", e); return []

    def get_venue(self, venue_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM ems_venues WHERE id=%s AND company_id=%s", (venue_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_venue: %s", e); return None

    def create_venue(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            vid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ems_venues(id,company_id,name,capacity,hourly_rate,layout_options,description) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                        (vid, company_id, data["name"], data.get("capacity",0), data.get("hourly_rate",0),
                         data.get("layout_options",""), data.get("description",""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_venue: %s", e); return None

    def update_venue(self, venue_id: str, company_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ems_venues SET name=%s,capacity=%s,hourly_rate=%s,layout_options=%s,description=%s WHERE id=%s AND company_id=%s",
                        (data["name"], data.get("capacity",0), data.get("hourly_rate",0),
                         data.get("layout_options",""), data.get("description",""), venue_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("update_venue: %s", e); return False

    def delete_venue(self, venue_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE ems_venues SET is_active=FALSE WHERE id=%s AND company_id=%s", (venue_id, company_id))
            return True
        except Exception as e:
            logger.error("delete_venue: %s", e); return False

    # Availability Check — core conflict resolution
    def check_availability(self, venue_id: str, setup_start: datetime, teardown_end: datetime,
                            exclude_booking_id: str = None) -> dict:
        """Returns {available: bool, conflicts: [booking_dicts]}"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    query = """
                        SELECT * FROM ems_bookings
                        WHERE venue_id=%s
                          AND status != 'cancelled'
                          AND teardown_end > %s
                          AND setup_start < %s
                    """
                    params = [venue_id, setup_start, teardown_end]
                    if exclude_booking_id:
                        query += " AND id != %s"
                        params.append(exclude_booking_id)
                    cur.execute(query, params)
                    conflicts = [dict(r) for r in cur.fetchall()]
                    return {"available": len(conflicts) == 0, "conflicts": conflicts}
        except Exception as e:
            logger.error("check_availability: %s", e)
            return {"available": False, "conflicts": []}

    # Bookings
    def get_bookings(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT b.*, v.name AS venue_name FROM ems_bookings b
                           JOIN ems_venues v ON b.venue_id=v.id
                           WHERE b.company_id=%s ORDER BY b.event_start DESC""",
                        (company_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_bookings: %s", e); return []

    def get_booking(self, booking_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT b.*, v.name AS venue_name FROM ems_bookings b
                           JOIN ems_venues v ON b.venue_id=v.id
                           WHERE b.id=%s AND b.company_id=%s""",
                        (booking_id, company_id)
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    bk = dict(row)
                    cur.execute("SELECT * FROM ems_booking_services WHERE booking_id=%s", (booking_id,))
                    bk["services"] = [dict(r) for r in cur.fetchall()]
                    return bk
        except Exception as e:
            logger.error("get_booking: %s", e); return None

    def create_booking(self, company_id: str, data: dict, service_ids: List[str], service_qtys: List[float]) -> dict:
        """Returns {ok, booking, error}. Checks conflicts first."""
        from datetime import datetime as _dt
        try:
            setup_start   = data["setup_start"]
            teardown_end  = data["teardown_end"]
            avail = self.check_availability(data["venue_id"], setup_start, teardown_end)
            if not avail["available"]:
                return {"ok": False, "error": "Conflict Detected — venue is already booked for that time block",
                        "conflicts": avail["conflicts"]}

            # Calculate hall rent
            venue = self.get_venue(data["venue_id"], company_id)
            hours = (data["event_end"] - data["event_start"]).total_seconds() / 3600
            hall_rent = round(float(venue["hourly_rate"]) * hours, 2) if venue else 0

            bid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO ems_bookings(id,company_id,venue_id,client_id,client_name,client_phone,event_name,
                           setup_start,event_start,event_end,teardown_end,status,hall_rent,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (bid, company_id, data["venue_id"], data.get("client_id","") or "", data["client_name"],
                         data.get("client_phone",""), data["event_name"],
                         setup_start, data["event_start"], data["event_end"], teardown_end,
                         data.get("status","tentative"), hall_rent, data.get("created_by",""))
                    )
                    booking = dict(cur.fetchone())

                    # Add services
                    services_total = 0.0
                    for sid, qty in zip(service_ids, service_qtys):
                        cur.execute("SELECT * FROM ems_service_items WHERE id=%s", (sid,))
                        svc = cur.fetchone()
                        if svc:
                            subtotal = round(float(svc["unit_price"]) * float(qty), 2)
                            services_total += subtotal
                            cur.execute(
                                "INSERT INTO ems_booking_services(id,booking_id,service_id,service_name,quantity,unit_price,subtotal) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                                (str(uuid.uuid4()), bid, sid, svc["name"], qty, svc["unit_price"], subtotal)
                            )

                    total = hall_rent + services_total
                    cur.execute(
                        "UPDATE ems_bookings SET services_total=%s, total_amount=%s WHERE id=%s",
                        (services_total, total, bid)
                    )
                    booking["services_total"] = services_total
                    booking["total_amount"] = total
            return {"ok": True, "booking": booking}
        except Exception as e:
            logger.error("create_booking: %s", e)
            return {"ok": False, "error": str(e)}

    def update_booking_status(self, booking_id: str, company_id: str, status: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ems_bookings SET status=%s WHERE id=%s AND company_id=%s",
                        (status, booking_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("update_booking_status: %s", e); return False

    # Service Items
    def get_services(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM ems_service_items WHERE company_id=%s AND is_active ORDER BY category, name", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_services: %s", e); return []

    def create_service(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            sid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ems_service_items(id,company_id,name,category,unit,unit_price) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
                        (sid, company_id, data["name"], data.get("category","other"),
                         data.get("unit","item"), data.get("unit_price",0))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_service: %s", e); return None

    # Analytics
    def get_occupancy(self, company_id: str, year: int, month: int) -> dict:
        """Calculate occupancy % for the month."""
        import calendar
        days_in_month = calendar.monthrange(year, month)[1]
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT COUNT(DISTINCT DATE(event_start)) AS booked_days
                           FROM ems_bookings
                           WHERE company_id=%s AND status='confirmed'
                           AND EXTRACT(YEAR FROM event_start)=%s
                           AND EXTRACT(MONTH FROM event_start)=%s""",
                        (company_id, year, month)
                    )
                    booked = cur.fetchone()["booked_days"] or 0
                    pct = round((booked / days_in_month) * 100, 1)
                    return {"booked_days": booked, "total_days": days_in_month, "occupancy_pct": pct}
        except Exception as e:
            logger.error("get_occupancy: %s", e)
            return {"booked_days": 0, "total_days": days_in_month, "occupancy_pct": 0}

    def get_stats(self, company_id: str) -> dict:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM ems_venues WHERE company_id=%s AND is_active", (company_id,))
                    venues = cur.fetchone()["c"]
                    cur.execute("SELECT COUNT(*) AS c FROM ems_bookings WHERE company_id=%s AND status='confirmed'", (company_id,))
                    confirmed = cur.fetchone()["c"]
                    cur.execute("SELECT COALESCE(SUM(total_amount),0) AS t FROM ems_bookings WHERE company_id=%s AND status='confirmed'", (company_id,))
                    revenue = float(cur.fetchone()["t"])
                    cur.execute("SELECT COUNT(*) AS c FROM ems_bookings WHERE company_id=%s AND status='tentative'", (company_id,))
                    tentative = cur.fetchone()["c"]
                    return {"venues": venues, "confirmed_bookings": confirmed, "tentative_bookings": tentative, "total_revenue": revenue}
        except Exception as e:
            logger.error("get_stats: %s", e)
            return {"venues": 0, "confirmed_bookings": 0, "tentative_bookings": 0, "total_revenue": 0}

    # Clients
    def get_clients(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM ems_clients WHERE company_id=%s ORDER BY name", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_clients: %s", e); return []

    def get_client(self, client_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM ems_clients WHERE id=%s AND company_id=%s", (client_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_client: %s", e); return None

    def create_client(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            cid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ems_clients(id,company_id,name,organization,phone,email,tin,notes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                        (cid, company_id, data["name"], data.get("organization",""), data.get("phone",""),
                         data.get("email",""), data.get("tin",""), data.get("notes",""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_client: %s", e); return None

    def update_client(self, client_id: str, company_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ems_clients SET name=%s,organization=%s,phone=%s,email=%s,tin=%s,notes=%s WHERE id=%s AND company_id=%s",
                        (data["name"], data.get("organization",""), data.get("phone",""), data.get("email",""),
                         data.get("tin",""), data.get("notes",""), client_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("update_client: %s", e); return False

    def get_client_bookings(self, company_id: str, client_id: str, client_name: str) -> List[dict]:
        """Booking history: match by client_id OR by exact client_name (legacy free-text bookings)."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT b.*, v.name AS venue_name FROM ems_bookings b
                           JOIN ems_venues v ON b.venue_id=v.id
                           WHERE b.company_id=%s AND (b.client_id=%s OR b.client_name=%s)
                           ORDER BY b.event_start DESC""",
                        (company_id, client_id, client_name)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_client_bookings: %s", e); return []

    # Reports
    def get_reports(self, company_id: str, start_date, end_date) -> dict:
        """All /ems/reports figures for the period [start_date, end_date] (dates)."""
        period_days = max((end_date - start_date).days + 1, 1)
        out = {
            "start": start_date, "end": end_date, "period_days": period_days,
            "revenue_by_month": [], "monthly_event_counts": [], "venue_utilization": [],
            "total_event_days": 0, "total_setup_days": 0, "total_teardown_days": 0,
            "total_idle_days": 0, "client_activity": [], "total_revenue": 0.0,
        }
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # Revenue by month (confirmed)
                    cur.execute(
                        """SELECT to_char(date_trunc('month', event_start),'YYYY-MM') AS month,
                                  COALESCE(SUM(total_amount),0) AS revenue
                           FROM ems_bookings
                           WHERE company_id=%s AND status='confirmed'
                             AND event_start::date BETWEEN %s AND %s
                           GROUP BY 1 ORDER BY 1""",
                        (company_id, start_date, end_date))
                    out["revenue_by_month"] = [dict(r) for r in cur.fetchall()]
                    out["total_revenue"] = float(sum(float(r["revenue"]) for r in out["revenue_by_month"]))

                    # Monthly event counts (non-cancelled)
                    cur.execute(
                        """SELECT to_char(date_trunc('month', event_start),'YYYY-MM') AS month,
                                  COUNT(*) AS events
                           FROM ems_bookings
                           WHERE company_id=%s AND status != 'cancelled'
                             AND event_start::date BETWEEN %s AND %s
                           GROUP BY 1 ORDER BY 1""",
                        (company_id, start_date, end_date))
                    out["monthly_event_counts"] = [dict(r) for r in cur.fetchall()]

                    # Day totals (bookings overlapping the period)
                    cur.execute(
                        """SELECT
                             COALESCE(SUM(event_end::date - event_start::date + 1),0)             AS event_days,
                             COALESCE(SUM(GREATEST(event_start::date - setup_start::date, 0)),0)  AS setup_days,
                             COALESCE(SUM(GREATEST(teardown_end::date - event_end::date, 0)),0)   AS teardown_days
                           FROM ems_bookings
                           WHERE company_id=%s AND status != 'cancelled'
                             AND setup_start::date <= %s AND teardown_end::date >= %s""",
                        (company_id, end_date, start_date))
                    row = cur.fetchone()
                    out["total_event_days"]    = int(row["event_days"] or 0)
                    out["total_setup_days"]    = int(row["setup_days"] or 0)
                    out["total_teardown_days"] = int(row["teardown_days"] or 0)

                    # Venue utilization: distinct booked days (incl. setup/teardown) per venue
                    cur.execute(
                        """SELECT v.id, v.name, COUNT(DISTINCT d.day) AS booked_days
                           FROM ems_venues v
                           LEFT JOIN ems_bookings b
                             ON b.venue_id=v.id AND b.company_id=v.company_id AND b.status != 'cancelled'
                            AND b.setup_start::date <= %s AND b.teardown_end::date >= %s
                           LEFT JOIN LATERAL generate_series(
                                GREATEST(b.setup_start::date, %s::date),
                                LEAST(b.teardown_end::date, %s::date),
                                interval '1 day') AS d(day) ON TRUE
                           WHERE v.company_id=%s AND v.is_active
                           GROUP BY v.id, v.name ORDER BY v.name""",
                        (end_date, start_date, start_date, end_date, company_id))
                    util = []
                    for r in cur.fetchall():
                        booked = int(r["booked_days"] or 0)
                        idle = max(period_days - booked, 0)
                        util.append({"id": r["id"], "name": r["name"], "booked_days": booked,
                                     "idle_days": idle,
                                     "utilization_pct": round(booked / period_days * 100, 1)})
                    out["venue_utilization"] = util
                    out["total_idle_days"] = sum(u["idle_days"] for u in util)

                    # Client activity
                    cur.execute(
                        """SELECT COALESCE(NULLIF(client_id,''), client_name) AS client_key,
                                  MAX(client_name) AS client_name,
                                  MAX(NULLIF(client_id,'')) AS client_id,
                                  COUNT(*) AS bookings,
                                  COALESCE(SUM(total_amount) FILTER (WHERE status='confirmed'),0) AS revenue
                           FROM ems_bookings
                           WHERE company_id=%s AND status != 'cancelled'
                             AND event_start::date BETWEEN %s AND %s
                           GROUP BY 1 ORDER BY revenue DESC, bookings DESC""",
                        (company_id, start_date, end_date))
                    out["client_activity"] = [dict(r) for r in cur.fetchall()]
            return out
        except Exception as e:
            logger.error("get_reports: %s", e)
            return out

    # Dashboard extras
    def get_upcoming_events(self, company_id: str, days: int = 14) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT b.*, v.name AS venue_name FROM ems_bookings b
                           JOIN ems_venues v ON b.venue_id=v.id
                           WHERE b.company_id=%s AND b.status != 'cancelled'
                             AND b.event_start >= NOW()
                             AND b.event_start < NOW() + make_interval(days => %s)
                           ORDER BY b.event_start""",
                        (company_id, days))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_upcoming_events: %s", e); return []

    def get_today_occupancy(self, company_id: str) -> List[dict]:
        """Per-venue status today: [{id, name, occupied, event_name, booking_id, status}]"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT v.id, v.name, b.id AS booking_id, b.event_name, b.status
                           FROM ems_venues v
                           LEFT JOIN ems_bookings b
                             ON b.venue_id=v.id AND b.status != 'cancelled'
                            AND b.setup_start::date <= CURRENT_DATE
                            AND b.teardown_end::date >= CURRENT_DATE
                           WHERE v.company_id=%s AND v.is_active
                           ORDER BY v.name, b.event_start""",
                        (company_id,))
                    seen = {}
                    for r in cur.fetchall():
                        if r["id"] in seen and not r["booking_id"]:
                            continue
                        if r["id"] in seen and seen[r["id"]]["occupied"]:
                            continue
                        seen[r["id"]] = {"id": r["id"], "name": r["name"],
                                         "occupied": bool(r["booking_id"]),
                                         "event_name": r["event_name"] or "",
                                         "booking_id": r["booking_id"] or "",
                                         "status": r["status"] or ""}
                    return list(seen.values())
        except Exception as e:
            logger.error("get_today_occupancy: %s", e); return []

    def get_revenue_projection(self, company_id: str) -> float:
        """Sum of tentative + confirmed future bookings."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT COALESCE(SUM(total_amount),0) AS projected
                           FROM ems_bookings
                           WHERE company_id=%s AND status IN ('tentative','confirmed')
                             AND event_start >= NOW()""",
                        (company_id,))
                    return float(cur.fetchone()["projected"])
        except Exception as e:
            logger.error("get_revenue_projection: %s", e); return 0.0

    # Visitors
    def get_visitors(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM ems_visitors WHERE company_id=%s ORDER BY checkin_at DESC LIMIT 200", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_visitors: %s", e); return []

    def register_visitor(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            vid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ems_visitors(id,company_id,name,phone,email,purpose,host) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                        (vid, company_id, data["name"], data.get("phone",""), data.get("email",""),
                         data.get("purpose",""), data.get("host",""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("register_visitor: %s", e); return None

    def checkout_visitor(self, visitor_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ems_visitors SET checkout_at=NOW() WHERE id=%s AND company_id=%s AND checkout_at IS NULL",
                        (visitor_id, company_id))
            return True
        except Exception as e:
            logger.error("checkout_visitor: %s", e); return False

    # Appointments
    def get_appointments(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM ems_appointments WHERE company_id=%s ORDER BY scheduled_at DESC LIMIT 200", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_appointments: %s", e); return []

    def create_appointment(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            aid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO ems_appointments(id,company_id,visitor_name,phone,email,host,scheduled_at,status,notes)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (aid, company_id, data["visitor_name"], data.get("phone",""), data.get("email",""),
                         data.get("host",""), data["scheduled_at"], data.get("status","pending"), data.get("notes",""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_appointment: %s", e); return None

    def update_appointment_status(self, appointment_id: str, company_id: str, status: str) -> bool:
        if status not in ("pending", "confirmed", "cancelled", "completed"):
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ems_appointments SET status=%s WHERE id=%s AND company_id=%s",
                        (status, appointment_id, company_id))
            return True
        except Exception as e:
            logger.error("update_appointment_status: %s", e); return False


ems_store = EMSDataStore()
