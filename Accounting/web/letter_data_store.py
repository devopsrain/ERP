"""
Letter & E-Signature Data Store — file-backed (JSON) for local MVP.

Stores:
 - Letters          : sequential numbered official letters
 - Signatures       : stored base64 PNG signatures for PM, FM, MD
 - Mail Tracker     : log of every sent/approved letter with timestamps
"""

import uuid
import json
import logging
import base64
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_DIR  = Path(__file__).parent / "data" / "letters"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_LETTERS_FILE   = _DATA_DIR / "letters.json"
_SIGS_FILE      = _DATA_DIR / "signatures.json"
_TRACKER_FILE   = _DATA_DIR / "mail_tracker.json"

# Ready-made letters uploaded as .docx/.pdf are stored here
UPLOADS_DIR = _DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# The three authorised signatories
SIGNATORIES = ["PM", "FM", "MD"]   # Project Manager, Finance Manager, Managing Director


# ── helpers ──────────────────────────────────────────────────────────────────

def _load(path: Path) -> list:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("_load %s: %s", path, e)
    return []


def _save(path: Path, data: list) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.error("_save %s: %s", path, e)


def _next_ref_number() -> str:
    """Auto-increment sequential reference number: REF-0001, REF-0002 …"""
    letters = _load(_LETTERS_FILE)
    highest = 0
    for ltr in letters:
        try:
            num = int(str(ltr.get("ref_number", "REF-0000")).split("-")[-1])
            highest = max(highest, num)
        except (ValueError, IndexError):
            pass
    return f"REF-{highest + 1:04d}"


# ── Signature store ───────────────────────────────────────────────────────────

def get_all_signatures() -> dict:
    """Return dict {role: {data_url, saved_at, saved_by}}."""
    rows = _load(_SIGS_FILE)
    return {r["role"]: r for r in rows if r.get("role") in SIGNATORIES}


def save_signature(role: str, data_url: str, saved_by: str) -> bool:
    """Upsert a signature image (data-URL) for a given signatory role."""
    if role not in SIGNATORIES:
        return False
    if not data_url.startswith("data:image/"):
        return False
    rows = [r for r in _load(_SIGS_FILE) if r.get("role") != role]
    rows.append({
        "role":     role,
        "data_url": data_url,
        "saved_at": datetime.now().isoformat(),
        "saved_by": saved_by,
    })
    _save(_SIGS_FILE, rows)
    return True


def delete_signature(role: str) -> bool:
    rows = [r for r in _load(_SIGS_FILE) if r.get("role") != role]
    _save(_SIGS_FILE, rows)
    return True


# ── Letter CRUD ───────────────────────────────────────────────────────────────

def get_all_letters(company_id: str = "default") -> list:
    return sorted(
        [l for l in _load(_LETTERS_FILE)],
        key=lambda l: l.get("created_at", ""),
        reverse=True,
    )


def get_letter_by_id(letter_id: str) -> Optional[dict]:
    return next((l for l in _load(_LETTERS_FILE) if l["letter_id"] == letter_id), None)


def create_letter(data: dict, created_by: str) -> dict:
    """Create a new letter draft."""
    letter = {
        "letter_id":   str(uuid.uuid4()),
        "ref_number":  _next_ref_number(),
        "date":        data.get("date", datetime.now().strftime("%Y-%m-%d")),
        "to":          data.get("to", "").strip(),
        "to_address":  data.get("to_address", "").strip(),
        "subject":     data.get("subject", "").strip(),
        "body":        data.get("body", "").strip(),
        "cc":          data.get("cc", "").strip(),
        "status":      "draft",          # draft → signed → sent
        "created_by":  created_by,
        "created_at":  datetime.now().isoformat(),
        "signatures":  {},               # {role: {signed_at, signed_by}}
        "sent_at":     None,
        "sent_by":     None,
        "company_id":  data.get("company_id", "default"),
    }
    letters = _load(_LETTERS_FILE)
    letters.append(letter)
    _save(_LETTERS_FILE, letters)
    return letter


def create_uploaded_letter(data: dict, created_by: str) -> dict:
    """
    Create a letter record for an already-prepared uploaded document
    (.docx / .pdf). Skips composition (no body) but keeps the normal
    sequential REF number and the standard draft → signed → sent flow.
    """
    letter = {
        "letter_id":         str(uuid.uuid4()),
        "ref_number":        _next_ref_number(),
        "date":              data.get("date", datetime.now().strftime("%Y-%m-%d")),
        "to":                data.get("to", "").strip(),
        "to_address":        data.get("to_address", "").strip(),
        "subject":           data.get("subject", "").strip(),
        "body":              "",
        "cc":                data.get("cc", "").strip(),
        "category":          data.get("category", "").strip(),
        "status":            "draft",     # draft → signed → sent
        "source":            "uploaded",  # marker: ready-made uploaded letter
        "stored_filename":   data.get("stored_filename", ""),
        "original_filename": data.get("original_filename", ""),
        "created_by":        created_by,
        "created_at":        datetime.now().isoformat(),
        "signatures":        {},
        "sent_at":           None,
        "sent_by":           None,
        "company_id":        data.get("company_id", "default"),
    }
    letters = _load(_LETTERS_FILE)
    letters.append(letter)
    _save(_LETTERS_FILE, letters)
    _log_tracker_event(letter, "uploaded", created_by,
                       f"Ready letter uploaded ({letter['original_filename']})")
    return letter


def update_letter(letter_id: str, updates: dict) -> Optional[dict]:
    letters = _load(_LETTERS_FILE)
    for i, ltr in enumerate(letters):
        if ltr["letter_id"] == letter_id:
            ltr.update(updates)
            letters[i] = ltr
            _save(_LETTERS_FILE, letters)
            return ltr
    return None


def sign_letter(letter_id: str, role: str, signed_by: str) -> Optional[dict]:
    """Attach a signatory's digital signature approval to a letter."""
    if role not in SIGNATORIES:
        return None
    letters = _load(_LETTERS_FILE)
    for i, ltr in enumerate(letters):
        if ltr["letter_id"] == letter_id:
            sigs = ltr.get("signatures", {})
            sigs[role] = {
                "signed_by": signed_by,
                "signed_at": datetime.now().isoformat(),
            }
            ltr["signatures"] = sigs
            # Auto-promote to "signed" when at least one sig exists
            if ltr["status"] == "draft":
                ltr["status"] = "signed"
            letters[i] = ltr
            _save(_LETTERS_FILE, letters)
            _log_tracker_event(ltr, "signed", signed_by, f"{role} signed")
            return ltr
    return None


def mark_sent(letter_id: str, sent_by: str) -> Optional[dict]:
    now = datetime.now().isoformat()
    ltr = update_letter(letter_id, {"status": "sent", "sent_at": now, "sent_by": sent_by})
    if ltr:
        _log_tracker_event(ltr, "sent", sent_by, "Letter marked as sent")
    return ltr


def delete_letter(letter_id: str) -> bool:
    all_letters = _load(_LETTERS_FILE)
    # Clean up the stored file for uploaded letters
    for ltr in all_letters:
        if ltr["letter_id"] == letter_id and ltr.get("stored_filename"):
            try:
                (UPLOADS_DIR / ltr["stored_filename"]).unlink(missing_ok=True)
            except Exception as e:
                logger.error("delete_letter: could not remove upload: %s", e)
    letters = [l for l in all_letters if l["letter_id"] != letter_id]
    _save(_LETTERS_FILE, letters)
    return True


def get_uploaded_file_path(letter: dict) -> Optional[Path]:
    """Return the on-disk path of an uploaded letter's file, if it exists."""
    name = (letter or {}).get("stored_filename", "")
    if not name:
        return None
    # basename() guards against any path traversal in stored data
    path = UPLOADS_DIR / Path(name).name
    return path if path.exists() else None


# ── Mail Tracker ─────────────────────────────────────────────────────────────

def _log_tracker_event(letter: dict, action: str, actor: str, details: str = "") -> None:
    tracker = _load(_TRACKER_FILE)
    tracker.append({
        "tracker_id": str(uuid.uuid4()),
        "letter_id":  letter["letter_id"],
        "ref_number": letter.get("ref_number", ""),
        "subject":    letter.get("subject", ""),
        "to":         letter.get("to", ""),
        "action":     action,
        "actor":      actor,
        "details":    details,
        "timestamp":  datetime.now().isoformat(),
    })
    _save(_TRACKER_FILE, tracker)


def get_tracker(letter_id: str = None) -> list:
    """Return all tracker events, optionally filtered by letter_id."""
    events = sorted(
        _load(_TRACKER_FILE),
        key=lambda e: e.get("timestamp", ""),
        reverse=True,
    )
    if letter_id:
        events = [e for e in events if e.get("letter_id") == letter_id]
    return events
