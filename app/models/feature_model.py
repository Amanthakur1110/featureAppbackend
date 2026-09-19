"""
Feature Model — MongoDB CRUD for Feature entities.

Each Feature document stores:
  - uid          : UUID4 string (used as the public folder name and URL identifier)
  - owner_id     : user's MongoDB _id string
  - name         : short human-readable feature name
  - description  : what the feature should do (user's original prompt)
  - status       : 'building' | 'clarifying' | 'ready' | 'error'
  - is_public    : bool — public features accessible without JWT
  - is_favourite : bool — starred by owner
  - chat_session : list of {role, content, timestamp} dicts (build conversation)
  - folder_path  : absolute server path to the feature's dist/ directory
  - error_msg    : last error message if status == 'error'
  - created_at   : datetime
  - updated_at   : datetime
"""

import uuid
from datetime import datetime, timezone
from bson import ObjectId
from app.db import get_db


# ─── Status Constants ────────────────────────────────────────────────────────

STATUS_BUILDING    = "building"
STATUS_CLARIFYING  = "clarifying"
STATUS_READY       = "ready"
STATUS_ERROR       = "error"


# ─── Serialization ────────────────────────────────────────────────────────────

def serialize_feature(feature: dict) -> dict:
    """Convert a MongoDB feature document into a JSON-serialisable dict."""
    if not feature:
        return None
    data = feature.copy()
    if "_id" in data:
        data["id"] = str(data.pop("_id"))
    for dt_field in ["created_at", "updated_at"]:
        if dt_field in data and isinstance(data[dt_field], datetime):
            data[dt_field] = data[dt_field].isoformat()
    # Serialise timestamps inside chat_session messages
    if "chat_session" in data:
        for msg in data["chat_session"]:
            if "timestamp" in msg and isinstance(msg["timestamp"], datetime):
                msg["timestamp"] = msg["timestamp"].isoformat()
    return data


# ─── Create ───────────────────────────────────────────────────────────────────

def create_feature(owner_id: str, name: str, description: str, folder_path: str) -> dict:
    """
    Insert a new Feature document and return it.
    Initial status is 'building'; chat_session starts with the user's description.
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    uid = str(uuid.uuid4())

    doc = {
        "uid":          uid,
        "owner_id":     owner_id,
        "name":         name.strip(),
        "description":  description.strip(),
        "status":       STATUS_BUILDING,
        "is_public":    False,
        "is_favourite": False,
        "chat_session": [
            {
                "role":      "user",
                "content":   description.strip(),
                "timestamp": now
            }
        ],
        "folder_path":  folder_path,
        "error_msg":    None,
        "created_at":   now,
        "updated_at":   now,
    }
    result = db.features.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


# ─── Read ─────────────────────────────────────────────────────────────────────

def find_feature_by_uid(uid: str) -> dict | None:
    """Find a feature by its public UID string."""
    db = get_db()
    return db.features.find_one({"uid": uid})


def find_feature_by_id(feature_id: str | ObjectId) -> dict | None:
    """Find a feature by MongoDB _id."""
    db = get_db()
    if isinstance(feature_id, str):
        try:
            feature_id = ObjectId(feature_id)
        except Exception:
            return None
    return db.features.find_one({"_id": feature_id})


def list_features_by_owner(owner_id: str) -> list[dict]:
    """Return all features owned by a user, sorted newest first."""
    db = get_db()
    cursor = db.features.find({"owner_id": owner_id}).sort("created_at", -1)
    return list(cursor)


# ─── Update ───────────────────────────────────────────────────────────────────

def update_feature_status(uid: str, status: str, error_msg: str = None) -> bool:
    """Update only the status (and optionally error_msg) of a feature."""
    db = get_db()
    update = {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}}
    if error_msg is not None:
        update["$set"]["error_msg"] = error_msg
    result = db.features.update_one({"uid": uid}, update)
    return result.modified_count > 0


def update_feature_fields(uid: str, fields: dict) -> dict | None:
    """
    General-purpose field update. Pass a dict of fields to $set.
    Returns the updated serialised document.
    """
    db = get_db()
    fields["updated_at"] = datetime.now(timezone.utc)
    updated = db.features.find_one_and_update(
        {"uid": uid},
        {"$set": fields},
        return_document=True
    )
    return serialize_feature(updated)


def append_chat_message(uid: str, role: str, content: str) -> bool:
    """
    Push a new message onto the feature's chat_session array.
    role must be 'user' or 'assistant'.
    """
    db = get_db()
    message = {
        "role":      role,
        "content":   content,
        "timestamp": datetime.now(timezone.utc)
    }
    result = db.features.update_one(
        {"uid": uid},
        {
            "$push": {"chat_session": message},
            "$set":  {"updated_at": datetime.now(timezone.utc)}
        }
    )
    return result.modified_count > 0


# ─── Delete ───────────────────────────────────────────────────────────────────

def delete_feature(uid: str, owner_id: str) -> bool:
    """
    Delete a feature document. Only the owner may delete.
    Returns True if a document was removed.
    """
    db = get_db()
    result = db.features.delete_one({"uid": uid, "owner_id": owner_id})
    return result.deleted_count > 0


# ─── DB Index Setup ───────────────────────────────────────────────────────────

def create_feature_indexes():
    """
    Create MongoDB indexes for the features collection.
    Called once at app startup from app/db/__init__.py.
    """
    db = get_db()
    db.features.create_index("uid",      unique=True)
    db.features.create_index("owner_id")
    db.features.create_index("status")
    db.features.create_index([("owner_id", 1), ("created_at", -1)])
