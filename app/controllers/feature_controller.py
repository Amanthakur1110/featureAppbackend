"""
Feature Controller — Business logic for feature CRUD and AI generation lifecycle.

Responsibilities:
  - Validate inputs
  - Interact with feature_model for DB operations
  - Trigger ai_generator for background generation
  - Enforce ownership / access control
  - Manage feature folder paths
"""

import os
import shutil
import logging
from datetime import datetime, timezone
from pathlib import Path
from flask import current_app, g

from app.models.feature_model import (
    create_feature,
    find_feature_by_uid,
    list_features_by_owner,
    update_feature_fields,
    update_feature_status,
    append_chat_message,
    delete_feature as db_delete_feature,
    serialize_feature,
    STATUS_BUILDING,
    STATUS_CLARIFYING,
)
from app.services.ai_generator import generate_feature_async, continue_after_clarification, cancel_generation
from app.utils.response import success_response, error_response

logger = logging.getLogger("feature_controller")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _feature_folder_path(uid: str) -> str:
    """
    Returns the absolute path to a feature's dist/ directory.
    All generated feature apps live under:  <project_root>/features/<uid>/dist/
    """
    # Go up from app/ to the project root (featureAppBackend/)
    project_root = Path(current_app.root_path).parent
    return str(project_root / "features" / uid / "dist")


def _can_read_feature(feature: dict, requesting_user_id: str | None) -> bool:
    """
    Access control:
      - Public features: anyone can read (requesting_user_id may be None)
      - Private features: only the owner can read
    """
    if feature.get("is_public"):
        return True
    return requesting_user_id is not None and feature.get("owner_id") == requesting_user_id


# ─── Create Feature ───────────────────────────────────────────────────────────

def ctrl_create_feature(user: dict, name: str, description: str):
    """
    Creates a feature document, kicks off background AI generation,
    and returns the initial feature metadata.
    """
    if not name or not name.strip():
        return error_response("Feature name is required", 400)
    if not description or not description.strip():
        return error_response("Feature description is required", 400)
    if len(name.strip()) > 120:
        return error_response("Feature name must be 120 characters or fewer", 400)

    owner_id = user["id"]
    folder_path = _feature_folder_path("__placeholder__")  # UID not known yet

    # Create DB document first to get the UID
    feature = create_feature(
        owner_id=owner_id,
        name=name,
        description=description,
        folder_path="",  # Updated below once UID is known
    )
    uid = feature["uid"]

    # Now set the real folder path
    real_folder = _feature_folder_path(uid)
    update_feature_fields(uid, {"folder_path": real_folder})

    # Kick off background generation
    generate_feature_async(
        uid=uid,
        name=name,
        description=description,
        folder_path=real_folder,
        app=current_app._get_current_object()
    )

    logger.info(f"Feature {uid} created by user {owner_id}, generation started")
    return success_response(
        data=serialize_feature(feature) | {"folder_path": real_folder},
        message="Feature creation started",
        status_code=201
    )


# ─── List Features ────────────────────────────────────────────────────────────

def ctrl_list_features(user: dict):
    """Returns all features owned by the authenticated user, newest first."""
    owner_id = user["id"]
    features = list_features_by_owner(owner_id)
    return success_response(
        data=[serialize_feature(f) for f in features],
        message="Features retrieved"
    )


# ─── Get Feature ──────────────────────────────────────────────────────────────

def ctrl_get_feature(uid: str, requesting_user_id: str | None):
    """
    Returns feature metadata.
    Public features: no auth needed.
    Private features: only owner.
    """
    feature = find_feature_by_uid(uid)
    if not feature:
        return error_response("Feature not found", 404)

    if not _can_read_feature(feature, requesting_user_id):
        return error_response("Access denied. This feature is private.", 403)

    return success_response(data=serialize_feature(feature), message="Feature retrieved")


# ─── Get Feature Status (Polling Endpoint) ────────────────────────────────────

def ctrl_get_feature_status(uid: str, owner_id: str):
    """
    Lightweight polling endpoint.
    Returns: status, and if status == 'clarifying', the pending question.
    """
    feature = find_feature_by_uid(uid)
    if not feature:
        return error_response("Feature not found", 404)
    if feature.get("owner_id") != owner_id:
        return error_response("Access denied", 403)

    status = feature.get("status")
    pending_question = None

    # If clarifying, the last message in chat_session is the AI's question
    if status == STATUS_CLARIFYING:
        session = feature.get("chat_session", [])
        for msg in reversed(session):
            if msg.get("role") == "assistant":
                pending_question = msg.get("content")
                break

    return success_response(data={
        "uid":              uid,
        "status":           status,
        "pending_question": pending_question,
        "error_msg":        feature.get("error_msg"),
    }, message="Status retrieved")


# ─── Send Clarification ───────────────────────────────────────────────────────

def ctrl_send_clarification(uid: str, owner_id: str, answer: str):
    """
    Called when the user replies to an AI clarification question.
    Saves the answer to the chat session and wakes the generation thread.
    If the worker thread was disconnected or status was interrupted, seamlessly
    re-triggers generation with the user's answer so the build NEVER fails.
    """
    if not answer or not answer.strip():
        return error_response("Answer cannot be empty", 400)

    feature = find_feature_by_uid(uid)
    if not feature:
        return error_response("Feature not found", 404)
    if feature.get("owner_id") != owner_id:
        return error_response("Access denied", 403)

    clean_answer = answer.strip()
    append_chat_message(uid, "user", clean_answer)

    woke = continue_after_clarification(uid, clean_answer)

    if woke:
        update_feature_status(uid, STATUS_BUILDING)
        logger.info(f"Clarification woke thread for {uid}, status updated to building")
        return success_response(message="Clarification received. Resuming generation.")
    else:
        # Seamless fallback: if no waiting thread, re-trigger via edit with appended clarification
        logger.warning(f"Clarification for {uid} had no active waiting thread, restarting via edit")
        combined_desc = (
            f"{feature.get('description', '')}\n\n"
            f"User clarification: {clean_answer}"
        )
        ctrl_edit_feature(uid, owner_id, combined_desc)
        return success_response(message="Clarification received. Resuming generation.")


# ─── Edit / Regenerate Feature ───────────────────────────────────────────────

def ctrl_edit_feature(uid: str, owner_id: str, new_description: str):
    """
    Re-generates an existing feature with a new description.

    Can be called from ANY feature status — even while building or clarifying.
    The cancellation system uses generation_id versioning so the new thread
    is never affected by the old cancellation.

    Steps:
      1. Validate ownership.
      2. Cancel the current generation thread (if any) via generation_id.
      3. Delete old dist/ files so no stale HTML is served while rebuilding.
      4. Reset DB: status → building, description updated, chat_session cleared.
      5. Kick off a fresh generation thread with the same UID/workspace.
    """
    if not new_description or not new_description.strip():
        return error_response("New description is required", 400)
    if len(new_description.strip()) < 10:
        return error_response("Description is too short. Please provide more detail.", 400)

    feature = find_feature_by_uid(uid)
    if not feature:
        return error_response("Feature not found", 404)
    if feature.get("owner_id") != owner_id:
        return error_response("Access denied", 403)

    previous_status = feature.get("status", "unknown")
    logger.info(f"Edit requested for {uid} (was: {previous_status})")

    # Step 1: Cancel any running generation thread atomically.
    # This marks the OLD generation_id as cancelled; the new thread gets a
    # fresh generation_id and will NOT be affected by the cancellation.
    cancel_generation(uid)

    # Step 2: Delete old dist/ so the WebView can't load stale HTML.
    folder_path = feature.get("folder_path", "")
    if folder_path:
        dist_dir = Path(folder_path)
        try:
            if dist_dir.exists():
                shutil.rmtree(dist_dir)
                logger.info(f"Edit {uid}: deleted old dist/ at {dist_dir}")
        except Exception as e:
            logger.warning(f"Edit {uid}: could not remove old dist/: {e}")

    name = feature["name"]
    desc = new_description.strip()

    # Step 3: Reset document for a fresh build — clear chat_session for clean slate.
    update_feature_fields(uid, {
        "description":  desc,
        "status":       STATUS_BUILDING,
        "error_msg":    None,
        "chat_session": [{
            "role":      "user",
            "content":   desc,
            "timestamp": datetime.now(timezone.utc)
        }],
    })

    # Step 4: Ensure folder_path is set (it always should be, but be safe).
    real_folder = folder_path or _feature_folder_path(uid)
    if not folder_path:
        update_feature_fields(uid, {"folder_path": real_folder})

    # Step 5: Spawn a fresh generation thread.
    generate_feature_async(
        uid=uid,
        name=name,
        description=desc,
        folder_path=real_folder,
        app=current_app._get_current_object()
    )

    logger.info(f"Feature {uid} edit started by {owner_id} (was: {previous_status})")
    return success_response(
        data={"uid": uid, "name": name, "status": STATUS_BUILDING},
        message="Feature edit started. Regenerating from scratch...",
        status_code=202
    )


# ─── Update Feature ───────────────────────────────────────────────────────────

def ctrl_update_feature(uid: str, owner_id: str, payload: dict):
    """
    Update editable feature fields: name, description, is_public, is_favourite.
    Only the owner may update.
    """
    feature = find_feature_by_uid(uid)
    if not feature:
        return error_response("Feature not found", 404)
    if feature.get("owner_id") != owner_id:
        return error_response("Access denied", 403)

    allowed = {"name", "description", "is_public", "is_favourite"}
    fields = {k: v for k, v in payload.items() if k in allowed}

    if not fields:
        return error_response("No valid fields provided for update", 400)
    if "name" in fields and (not fields["name"] or not fields["name"].strip()):
        return error_response("Feature name cannot be empty", 400)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    if "description" in fields:
        fields["description"] = fields["description"].strip()

    updated = update_feature_fields(uid, fields)
    return success_response(data=updated, message="Feature updated")


# ─── Delete Feature ───────────────────────────────────────────────────────────

def ctrl_delete_feature(uid: str, owner_id: str):
    """
    Deletes the feature document and its generated files from disk.
    Only the owner may delete.
    """
    feature = find_feature_by_uid(uid)
    if not feature:
        return error_response("Feature not found", 404)
    if feature.get("owner_id") != owner_id:
        return error_response("Access denied", 403)

    # Delete from DB
    deleted = db_delete_feature(uid, owner_id)
    if not deleted:
        return error_response("Failed to delete feature", 500)

    # Delete files from disk
    folder = feature.get("folder_path")
    if folder:
        feature_dir = Path(folder).parent  # Parent of dist/ is the feature uid dir
        try:
            if feature_dir.exists():
                shutil.rmtree(feature_dir)
                logger.info(f"Deleted feature folder: {feature_dir}")
        except Exception as e:
            logger.warning(f"Could not delete feature folder {feature_dir}: {e}")

    # Delete workspace directory
    ws_base = os.getenv("WORKSPACE_BASE_DIR")
    ws_dir = Path(ws_base) / uid if ws_base else None
    if not ws_dir or not ws_dir.exists():
        if Path("/app/workspaces").exists():
            ws_dir = Path("/app/workspaces") / uid
        else:
            ws_dir = Path(__file__).resolve().parent.parent.parent / "workspaces" / uid
    if ws_dir and ws_dir.exists():
        try:
            shutil.rmtree(ws_dir)
            logger.info(f"Deleted feature workspace: {ws_dir}")
        except Exception as e:
            logger.warning(f"Could not delete workspace {ws_dir}: {e}")

    return success_response(message="Feature deleted successfully")


# ─── Clear Feature Storage (Signal) ──────────────────────────────────────────

def ctrl_clear_feature_storage(uid: str, owner_id: str):
    """
    Sets a 'clear_storage' flag in the DB.
    The Android app checks this flag when opening the WebView and clears
    the AndroidStorage data for this feature, then resets the flag.
    """
    feature = find_feature_by_uid(uid)
    if not feature:
        return error_response("Feature not found", 404)
    if feature.get("owner_id") != owner_id:
        return error_response("Access denied", 403)

    update_feature_fields(uid, {"clear_storage_requested": True})
    return success_response(message="Storage clear requested. Will take effect on next open.")


# ─── Acknowledge Storage Cleared ─────────────────────────────────────────────

def ctrl_ack_storage_cleared(uid: str, owner_id: str):
    """
    Called by the app after it has cleared the feature's AndroidStorage.
    Resets the clear_storage_requested flag.
    """
    feature = find_feature_by_uid(uid)
    if not feature:
        return error_response("Feature not found", 404)
    if feature.get("owner_id") != owner_id:
        return error_response("Access denied", 403)

    update_feature_fields(uid, {"clear_storage_requested": False})
    return success_response(message="Storage cleared acknowledged")
