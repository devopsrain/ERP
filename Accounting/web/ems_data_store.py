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
                        """INSERT INTO ems_bookings(id,company_id,venue_id,client_name,client_phone,event_name,
                           setup_start,event_start,event_end,teardown_end,status,hall_rent,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (bid, company_id, data["venue_id"], data["client_name"],
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


ems_store = EMSDataStore()
