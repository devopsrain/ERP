"""
Sales Data Store — PostgreSQL backend

Persists sales leads / contact-form submissions from the public landing page.
No authentication required to write; reading is provider-admin only.
"""

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from db import get_cursor

logger = logging.getLogger(__name__)


class SalesDataStore:

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save_contact(self, data: dict) -> Optional[str]:
        """
        Persist a contact / demo-request submission.
        Returns the generated contact_id, or None on failure.
        """
        contact_id = str(uuid.uuid4())
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO sales_contacts
                       (contact_id, full_name, email, company_name,
                        tier_interest, message, ip_address, submitted_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        contact_id,
                        data.get('name', '').strip()[:200],
                        data.get('email', '').strip()[:254],
                        data.get('company', '').strip()[:200],
                        data.get('tier', '').strip()[:50],
                        data.get('message', '').strip()[:2000],
                        data.get('ip_address', ''),
                        datetime.utcnow(),
                    ),
                )
            logger.info("Sales lead saved: id=%s email=%s", contact_id, data.get('email'))
            return contact_id
        except Exception as e:
            logger.error("save_contact failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Read (provider-admin use)
    # ------------------------------------------------------------------

    def get_all_contacts(self, limit: int = 200) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT contact_id, full_name, email, company_name,
                              tier_interest, message, ip_address, submitted_at
                       FROM sales_contacts
                       ORDER BY submitted_at DESC
                       LIMIT %s""",
                    (limit,),
                )
                rows = cur.fetchall()
            return [dict(r) for r in rows] if rows else []
        except Exception as e:
            logger.error("get_all_contacts failed: %s", e)
            return []

    def get_contact_count(self) -> int:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM sales_contacts")
                row = cur.fetchone()
            return int(row['n']) if row else 0
        except Exception as e:
            logger.error("get_contact_count failed: %s", e)
            return 0


sales_store = SalesDataStore()
