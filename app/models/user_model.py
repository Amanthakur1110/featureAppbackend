from datetime import datetime, timezone
from bson import ObjectId
from app.db import get_db


def serialize_user(user: dict) -> dict:
    """Converts MongoDB user document into a clean, JSON-serializable dict."""
    if not user:
        return None
    data = user.copy()
    if "_id" in data:
        data["id"] = str(data.pop("_id"))
    for dt_field in ["created_at", "last_login", "updated_at"]:
        if dt_field in data and isinstance(data[dt_field], datetime):
            data[dt_field] = data[dt_field].isoformat()
    return data


def find_user_by_email(email: str) -> dict | None:
    """Finds user by email address (case-insensitive)."""
    db = get_db()
    return db.users.find_one({"email": email.strip().lower()})


def find_user_by_id(user_id: str | ObjectId) -> dict | None:
    """Finds user by ObjectId or string ID."""
    db = get_db()
    if isinstance(user_id, str):
        try:
            user_id = ObjectId(user_id)
        except Exception:
            return None
    return db.users.find_one({"_id": user_id})


def get_or_create_user(email: str) -> tuple[dict, bool]:
    """
    Finds user by email or creates a new user if one doesn't exist.
    Returns (user_dict, is_new_user_bool).
    """
    db = get_db()
    clean_email = email.strip().lower()
    existing_user = db.users.find_one({"email": clean_email})
    if existing_user:
        return existing_user, False

    now = datetime.now(timezone.utc)
    new_user = {
        "email": clean_email,
        "is_active": True,
        "role": "user",
        "created_at": now,
        "last_login": None,
        "profile": {
            "display_name": clean_email.split("@")[0],
            "bio": ""
        }
    }
    result = db.users.insert_one(new_user)
    new_user["_id"] = result.inserted_id
    return new_user, True


def update_last_login(email: str) -> bool:
    """Updates the user's last_login timestamp."""
    db = get_db()
    clean_email = email.strip().lower()
    now = datetime.now(timezone.utc)
    result = db.users.update_one(
        {"email": clean_email},
        {"$set": {"last_login": now}}
    )
    return result.modified_count > 0


def update_user_profile(email: str, profile_data: dict) -> dict | None:
    """Updates profile information for a user."""
    db = get_db()
    clean_email = email.strip().lower()
    now = datetime.now(timezone.utc)

    update_fields = {}
    for k, v in profile_data.items():
        update_fields[f"profile.{k}"] = v
    update_fields["updated_at"] = now

    updated = db.users.find_one_and_update(
        {"email": clean_email},
        {"$set": update_fields},
        return_document=True
    )
    return serialize_user(updated)
