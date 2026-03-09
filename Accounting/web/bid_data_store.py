"""
Bid Data Store - PostgreSQL backend (schema matches init_db.sql bid_records/bid_documents_meta)

Document storage strategy:
  - If S3_BUCKET_BID_DOCS env var is set: files go to S3; downloads are pre-signed URLs.
  - Otherwise: files are written to web/data/bid_docs/ on the local disk (dev/fallback).
"""

import logging
import os
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from db import get_cursor, get_conn

logger = logging.getLogger(__name__)

# ── Storage backend detection ─────────────────────────────────────
try:
    import boto3 as _boto3
    from botocore.exceptions import ClientError as _S3Error
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False

S3_BUCKET      = os.environ.get('S3_BUCKET_BID_DOCS', '')
S3_REGION      = os.environ.get('AWS_DEFAULT_REGION', 'af-south-1')
PRESIGNED_TTL  = 3600          # seconds for pre-signed download URLs
BID_DOCS_DIR   = Path(__file__).parent / "data" / "bid_docs"  # used when S3 disabled


def _use_s3() -> bool:
    """Return True when S3 storage is configured and boto3 is available."""
    return _BOTO3_AVAILABLE and bool(S3_BUCKET)


def _s3() :
    return _boto3.client('s3', region_name=S3_REGION)


def _s3_key(bid_id: str, stored_name: str) -> str:
    return f"bid_docs/{bid_id}/{stored_name}"


class BidDataStore:
    """PostgreSQL-backed bid/tender management."""

    def __init__(self, data_dir=None):
        BID_DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Bids
    # ------------------------------------------------------------------
    def get_all_bids(self, company_id: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM bid_records WHERE company_id=%s ORDER BY created_at DESC LIMIT 500",
                    (cid,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_all_bids failed: %s", e)
            return []

    def get_bid_by_id(self, bid_id: str, company_id: str = None) -> Optional[dict]:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM bid_records WHERE id=%s AND company_id=%s",
                    (bid_id, cid)
                )
                row = cur.fetchone()
                if not row:
                    return None
                bid = dict(row)
                cur.execute(
                    "SELECT * FROM bid_documents_meta WHERE bid_id=%s ORDER BY uploaded_at DESC",
                    (bid_id,)
                )
                bid['documents'] = [dict(r) for r in cur.fetchall()]
                return bid
        except Exception as e:
            logger.error("get_bid_by_id failed: %s", e)
            return None

    def save_bid(self, data: dict, company_id: str = None) -> Optional[str]:
        """Create or update a bid. Returns bid id on success."""
        cid = company_id or data.get('company_id', 'default')
        bid_id = data.get('id') or str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        try:
            with get_cursor() as cur:
                cur.execute("SELECT 1 FROM bid_records WHERE id=%s", (bid_id,))
                exists = cur.fetchone()
                if exists:
                    cur.execute(
                        """UPDATE bid_records SET
                           title=%s, reference_number=%s, organization=%s,
                           description=%s, category=%s, status=%s,
                           deadline=%s, submission_date=%s, bid_amount=%s,
                           currency=%s, case_handler_name=%s, case_handler_email=%s,
                           reminder_days_before=%s, notes=%s, updated_at=%s
                           WHERE id=%s""",
                        (data.get('title', ''),
                         data.get('reference_number', ''),
                         data.get('organization', ''),
                         data.get('description', ''),
                         data.get('category', ''),
                         data.get('status', 'open'),
                         data.get('deadline', ''),
                         data.get('submission_date', ''),
                         float(data.get('bid_amount', 0)),
                         data.get('currency', 'ETB'),
                         data.get('case_handler_name', ''),
                         data.get('case_handler_email', ''),
                         int(data.get('reminder_days_before', 3)),
                         data.get('notes', ''),
                         now, bid_id)
                    )
                else:
                    cur.execute(
                        """INSERT INTO bid_records
                           (id, company_id, title, reference_number, organization,
                            description, category, status, deadline, submission_date,
                            bid_amount, currency, case_handler_name, case_handler_email,
                            reminder_days_before, reminder_sent, notes, created_at, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (bid_id, cid,
                         data.get('title', ''),
                         data.get('reference_number', ''),
                         data.get('organization', ''),
                         data.get('description', ''),
                         data.get('category', ''),
                         data.get('status', 'open'),
                         data.get('deadline', ''),
                         data.get('submission_date', ''),
                         float(data.get('bid_amount', 0)),
                         data.get('currency', 'ETB'),
                         data.get('case_handler_name', ''),
                         data.get('case_handler_email', ''),
                         int(data.get('reminder_days_before', 3)),
                         False,
                         data.get('notes', ''),
                         now, now)
                    )
            return bid_id
        except Exception as e:
            logger.error("save_bid failed: %s", e)
            return None

    def delete_bid(self, bid_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            # Delete documents from S3 or disk before removing DB records
            with get_cursor() as cur:
                cur.execute(
                    "SELECT filename FROM bid_documents_meta WHERE bid_id=%s", (bid_id,)
                )
                docs = cur.fetchall() or []

            for doc in docs:
                if _use_s3():
                    try:
                        _s3().delete_object(
                            Bucket=S3_BUCKET,
                            Key=_s3_key(bid_id, doc['filename'])
                        )
                    except Exception as ex:
                        logger.warning("S3 delete failed for bid %s doc %s: %s",
                                       bid_id, doc['filename'], ex)
                else:
                    fp = BID_DOCS_DIR / bid_id / doc['filename']
                    if fp.exists():
                        fp.unlink()

            # Remove local folder if any disk files remain
            if not _use_s3():
                docs_folder = BID_DOCS_DIR / bid_id
                if docs_folder.exists():
                    shutil.rmtree(docs_folder)

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bid_documents_meta WHERE bid_id=%s", (bid_id,))
                    cur.execute(
                        "DELETE FROM bid_records WHERE id=%s AND company_id=%s",
                        (bid_id, cid)
                    )
            return True
        except Exception as e:
            logger.error("delete_bid failed: %s", e)
            return False

    def get_summary_stats(self, company_id: str = None) -> dict:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT status, COUNT(*) AS cnt,
                       COALESCE(SUM(bid_amount),0) AS total
                       FROM bid_records WHERE company_id=%s GROUP BY status""",
                    (cid,)
                )
                stats = {
                    'total': 0, 'open': 0, 'submitted': 0,
                    'won': 0, 'lost': 0, 'total_value': 0.0, 'by_status': {}
                }
                for row in cur.fetchall():
                    s = row['status'].lower()
                    stats['by_status'][s] = {'count': row['cnt'], 'value': float(row['total'])}
                    stats['total'] += row['cnt']
                    stats['total_value'] += float(row['total'])
                    if s in stats:
                        stats[s] = row['cnt']
                return stats
        except Exception as e:
            logger.error("get_summary_stats failed: %s", e)
            return {
                'total': 0, 'open': 0, 'submitted': 0,
                'won': 0, 'lost': 0, 'total_value': 0.0, 'by_status': {}
            }

    # ------------------------------------------------------------------
    # Documents  (S3 or disk fallback)
    # ------------------------------------------------------------------
    def save_document(self, bid_id: str, file, doc_type: str,
                      description: str, uploaded_by: str) -> Optional[str]:
        try:
            doc_id = str(uuid.uuid4())
            ext = ('.' + file.filename.rsplit('.', 1)[1].lower()) if '.' in file.filename else ''
            stored_name = doc_id + ext
            file_data = file.read()
            file_size = len(file_data)

            if _use_s3():
                _s3().put_object(
                    Bucket=S3_BUCKET,
                    Key=_s3_key(bid_id, stored_name),
                    Body=file_data,
                    ContentDisposition=f'attachment; filename="{file.filename}"',
                )
            else:
                folder = BID_DOCS_DIR / bid_id
                folder.mkdir(parents=True, exist_ok=True)
                (folder / stored_name).write_bytes(file_data)

            now = datetime.utcnow().isoformat()
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO bid_documents_meta
                       (id, bid_id, filename, original_filename, doc_type,
                        description, uploaded_by, file_size, uploaded_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (doc_id, bid_id, stored_name, file.filename,
                     doc_type, description, uploaded_by, file_size, now)
                )
            return doc_id
        except Exception as e:
            logger.error("save_document failed: %s", e)
            return None

    def get_presigned_url(self, bid_id: str, doc_id: str) -> Optional[str]:
        """Return a pre-signed S3 download URL (S3 mode only), or None for disk mode."""
        if not _use_s3():
            return None
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT filename, original_filename FROM bid_documents_meta WHERE id=%s AND bid_id=%s",
                    (doc_id, bid_id)
                )
                row = cur.fetchone()
            if not row:
                return None
            return _s3().generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': S3_BUCKET,
                    'Key': _s3_key(bid_id, row['filename']),
                    'ResponseContentDisposition':
                        f'attachment; filename="{row["original_filename"]}"',
                },
                ExpiresIn=PRESIGNED_TTL,
            )
        except Exception as e:
            logger.error("get_presigned_url failed: %s", e)
            return None

    def get_document_path(self, bid_id: str, doc_id: str) -> Optional[str]:
        """Return the disk path for a document (disk mode only)."""
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT filename FROM bid_documents_meta WHERE id=%s AND bid_id=%s",
                    (doc_id, bid_id)
                )
                row = cur.fetchone()
                if not row:
                    return None
                path = BID_DOCS_DIR / bid_id / row['filename']
                return str(path) if path.exists() else None
        except Exception as e:
            logger.error("get_document_path failed: %s", e)
            return None

    def get_document_meta(self, doc_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM bid_documents_meta WHERE id=%s", (doc_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_document_meta failed: %s", e)
            return None

    def delete_document(self, doc_id: str) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT bid_id, filename FROM bid_documents_meta WHERE id=%s", (doc_id,)
                )
                row = cur.fetchone()
                if row:
                    if _use_s3():
                        try:
                            _s3().delete_object(
                                Bucket=S3_BUCKET,
                                Key=_s3_key(row['bid_id'], row['filename'])
                            )
                        except Exception as ex:
                            logger.warning("S3 delete failed for doc %s: %s", doc_id, ex)
                    else:
                        fp = BID_DOCS_DIR / row['bid_id'] / row['filename']
                        if fp.exists():
                            fp.unlink()
                cur.execute("DELETE FROM bid_documents_meta WHERE id=%s", (doc_id,))
            return True
        except Exception as e:
            logger.error("delete_document failed: %s", e)
            return False

    def send_test_email(self, email: str) -> dict:
        return {
            'success': False,
            'message': 'Email delivery is not configured on this server. Contact the system admin to set up SMTP.',
        }


# Singleton
bid_store = BidDataStore()
