from datetime import datetime, timezone, timedelta
from bson import ObjectId
from app.db import get_db


def create_verification_session(email: str, token: str, code: str, expires_in_minutes: int = 10) -> dict:
    """
    Creates a new temporary verification session record in MongoDB.
    Invalidates any previous pending sessions for this email to prevent reuse.
    Stores the token, the OTP code, expiration time, used=False, and status='active'.
    """
    db = get_db()
    clean_email = email.strip().lower()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=expires_in_minutes)

    # Invalidate any previously active unused sessions for this email
    db.verification_tokens.update_many(
        {"email": clean_email, "used": False},
        {"$set": {"used": True, "status": "superseded", "superseded_at": now}}
    )

    session_doc = {
        "email": clean_email,
        "token": token,
        "code": str(code).strip(),
        "used": False,
        "status": "active",
        "attempts": 0,
        "max_attempts": 5,
        "created_at": now,
        "expires_at": expires_at
    }

    result = db.verification_tokens.insert_one(session_doc)
    session_doc["_id"] = result.inserted_id
    return session_doc


def find_verification_session(email: str, token: str) -> dict | None:
    """
    Finds a verification session by email and token.
    """
    db = get_db()
    return db.verification_tokens.find_one({
        "email": email.strip().lower(),
        "token": token.strip()
    })


def mark_session_used(session_id: str | ObjectId) -> bool:
    """
    Atomically marks a verification session as used so it cannot be used again.
    Returns True only if the session was previously unused, preventing concurrency/replay.
    """
    db = get_db()
    if isinstance(session_id, str):
        session_id = ObjectId(session_id)

    now = datetime.now(timezone.utc)
    # Atomic condition: update only if used is currently False
    result = db.verification_tokens.update_one(
        {"_id": session_id, "used": False},
        {"$set": {"used": True, "status": "used", "used_at": now}}
    )
    return result.modified_count > 0


def record_failed_attempt(session_id: str | ObjectId) -> tuple[int, bool]:
    """
    Increments failed attempt count for a verification session.
    If max attempts (5) exceeded, atomically marks used=True and locks the session.
    Returns (current_attempts, is_locked).
    """
    db = get_db()
    if isinstance(session_id, str):
        session_id = ObjectId(session_id)

    now = datetime.now(timezone.utc)
    updated = db.verification_tokens.find_one_and_update(
        {"_id": session_id},
        {"$inc": {"attempts": 1}},
        return_document=True
    )
    if not updated:
        return 0, True

    attempts = updated.get("attempts", 1)
    max_attempts = updated.get("max_attempts", 5)

    if attempts >= max_attempts:
        db.verification_tokens.update_one(
            {"_id": session_id},
            {"$set": {"used": True, "status": "exceeded_max_attempts", "locked_at": now}}
        )
        return attempts, True

    return attempts, False
