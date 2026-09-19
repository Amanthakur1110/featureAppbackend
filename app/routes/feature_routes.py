"""
Feature Routes — All /features/* endpoints and static feature serving.

Endpoints:
  POST   /features/create              — Create feature + start building
  GET    /features/                    — List user's features
  GET    /features/<uid>               — Get feature metadata
  PUT    /features/<uid>               — Update name/visibility/favourite
  DELETE /features/<uid>               — Delete feature + files
  GET    /features/<uid>/status        — Poll build status (for home screen polling)
  POST   /features/<uid>/clarify       — User answers AI clarification question
  POST   /features/<uid>/clear-storage — Request AndroidStorage clear
  POST   /features/<uid>/ack-cleared   — App confirms storage was cleared

  GET    /feature/get/<uid>            — Serve the generated static dist/index.html
                                         (NOTE: different base path — no 's' — so the URL
                                          matches what the WebView loads)
"""

import os
from pathlib import Path
from flask import Blueprint, request, g, send_from_directory, abort

from app.middlewares.auth_middleware import token_required
from app.utils.response import error_response
from app.controllers.feature_controller import (
    ctrl_create_feature,
    ctrl_list_features,
    ctrl_get_feature,
    ctrl_get_feature_status,
    ctrl_send_clarification,
    ctrl_edit_feature,
    ctrl_update_feature,
    ctrl_delete_feature,
    ctrl_clear_feature_storage,
    ctrl_ack_storage_cleared,
)
from app.utils.jwt_helper import decode_jwt_token
from app.models.user_model import find_user_by_email, serialize_user
from app.models.feature_model import find_feature_by_uid

# Blueprint for authenticated feature CRUD — registered under /api/v1/features
feature_bp = Blueprint("features", __name__, url_prefix="/features")

# Separate blueprint for the static feature serving endpoint
# Registered at root level (not under /api/v1) so the URL is clean:
# GET http://server:8004/feature/get/<uid>
static_feature_bp = Blueprint("feature_static", __name__, url_prefix="/feature")


# ─── CRUD Endpoints ───────────────────────────────────────────────────────────

@feature_bp.route("/create", methods=["POST"])
@token_required
def create_feature():
    """Create a new feature and start AI generation."""
    data = request.get_json(silent=True) or {}
    return ctrl_create_feature(
        user=g.current_user,
        name=data.get("name", ""),
        description=data.get("description", "")
    )


@feature_bp.route("", methods=["GET"])
@feature_bp.route("/", methods=["GET"])
@token_required
def list_features():
    """List all features owned by the authenticated user."""
    return ctrl_list_features(user=g.current_user)


@feature_bp.route("/<uid>", methods=["GET"])
def get_feature(uid: str):
    """
    Get feature metadata.
    Public features: no token needed.
    Private features: JWT token required.
    """
    requesting_user_id = _extract_optional_user_id()
    return ctrl_get_feature(uid=uid, requesting_user_id=requesting_user_id)


@feature_bp.route("/<uid>", methods=["PUT"])
@token_required
def update_feature(uid: str):
    """Update feature fields (name, description, is_public, is_favourite)."""
    data = request.get_json(silent=True) or {}
    return ctrl_update_feature(
        uid=uid,
        owner_id=g.current_user["id"],
        payload=data
    )


@feature_bp.route("/<uid>", methods=["DELETE"])
@token_required
def delete_feature(uid: str):
    """Delete a feature and its generated files."""
    return ctrl_delete_feature(uid=uid, owner_id=g.current_user["id"])


# ─── Status & Clarification ───────────────────────────────────────────────────

@feature_bp.route("/<uid>/status", methods=["GET"])
@token_required
def get_feature_status(uid: str):
    """
    Poll the build status of a feature.
    Returns: { status, pending_question (if clarifying), error_msg }
    """
    return ctrl_get_feature_status(uid=uid, owner_id=g.current_user["id"])


@feature_bp.route("/<uid>/clarify", methods=["POST"])
@token_required
def send_clarification(uid: str):
    """User submits their answer to an AI clarification question."""
    data = request.get_json(silent=True) or {}
    return ctrl_send_clarification(
        uid=uid,
        owner_id=g.current_user["id"],
        answer=data.get("answer", "")
    )


# ─── Storage Management ───────────────────────────────────────────────────────

@feature_bp.route("/<uid>/clear-storage", methods=["POST"])
@token_required
def clear_feature_storage(uid: str):
    """Request that the app clears the AndroidStorage for this feature."""
    return ctrl_clear_feature_storage(uid=uid, owner_id=g.current_user["id"])


@feature_bp.route("/<uid>/ack-cleared", methods=["POST"])
@token_required
def ack_storage_cleared(uid: str):
    """App confirms it has cleared the feature storage."""
    return ctrl_ack_storage_cleared(uid=uid, owner_id=g.current_user["id"])


@feature_bp.route("/<uid>/edit", methods=["POST"])
@token_required
def edit_feature(uid: str):
    """
    Re-generate an existing feature with a new description.
    The same UID and workspace are reused; old dist/ is deleted and rebuilt.
    """
    data = request.get_json(silent=True) or {}
    return ctrl_edit_feature(
        uid=uid,
        owner_id=g.current_user["id"],
        new_description=data.get("description", "")
    )


# ─── Static Feature Serving ───────────────────────────────────────────────────

@static_feature_bp.route("/get/<uid>", methods=["GET"])
@static_feature_bp.route("/get/<uid>/", methods=["GET"])
def serve_feature(uid: str):
    """
    Serves the generated index.html for a feature.

    Access control:
      - Public features or features accessed by valid unguessable UID are served.
      - Injects <base href="/feature/get/<uid>/"> into <head> so all relative
        assets (./assets/...) resolve accurately in WebView/browser without 404s.
    """
    feature = find_feature_by_uid(uid)
    if not feature:
        abort(404)

    folder_path = feature.get("folder_path", "")
    if not folder_path:
        abort(404)

    dist_dir = Path(folder_path)
    index_file = dist_dir / "index.html"
    if not dist_dir.exists() or not index_file.exists():
        abort(404)

    # Read index.html and inject <base href="/feature/get/{uid}/"> and debug error listeners
    html_content = index_file.read_text(encoding="utf-8")
    debug_tag = f"""<base href="/feature/get/{uid}/">
    <script>
      window.addEventListener('error', function(e) {{
        if (e.target && (e.target.tagName === 'SCRIPT' || e.target.tagName === 'LINK')) {{
          console.error('[RESOURCE LOAD ERROR] Failed to load ' + e.target.tagName + ': ' + (e.target.src || e.target.href));
        }} else {{
          console.error('[WINDOW ONERROR] ' + (e.message || e.error) + ' at ' + (e.filename || '') + ':' + (e.lineno || 0) + (e.error && e.error.stack ? '\\n' + e.error.stack : ''));
        }}
      }}, true);
      window.addEventListener('unhandledrejection', function(e) {{
        console.error('[UNHANDLED PROMISE] ' + (e.reason && e.reason.stack ? e.reason.stack : e.reason));
      }});
      console.log('[DEBUG-HEAD] Diagnostics active. Base: /feature/get/{uid}/');
    </script>"""
    if "<base " not in html_content and "<base>" not in html_content:
        if "<head>" in html_content:
            html_content = html_content.replace("<head>", f"<head>\n    {debug_tag}", 1)
        elif "<head " in html_content:
            idx = html_content.find(">")
            html_content = html_content[:idx + 1] + f"\n    {debug_tag}" + html_content[idx + 1:]
        else:
            html_content = f"{debug_tag}\n" + html_content

    from flask import Response
    return Response(
        html_content,
        mimetype="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


@static_feature_bp.route("/get/<uid>/<path:filename>", methods=["GET"])
def serve_feature_file(uid: str, filename: str):
    """Serve relative static files (JS, CSS, images) from feature folder."""
    feature = find_feature_by_uid(uid)
    if not feature:
        abort(404)

    folder_path = feature.get("folder_path", "")
    if not folder_path:
        abort(404)

    dist_dir = Path(folder_path)
    file_path = dist_dir / filename
    if not file_path.exists():
        abort(404)

    return send_from_directory(str(dist_dir), filename)


@static_feature_bp.route("/get/assets/<path:filename>", methods=["GET"])
def serve_feature_asset_fallback(filename: str):
    """
    Fallback for relative asset requests when client resolved against /feature/get/
    instead of /feature/get/<uid>/.
    Extracts UID from Referer header or finds the corresponding feature folder.
    """
    import re
    referer = request.headers.get("Referer", "")
    match = re.search(r"/feature/get/([a-zA-Z0-9_-]+)", referer)
    if match:
        uid = match.group(1)
        feature = find_feature_by_uid(uid)
        if feature:
            dist_dir = Path(feature.get("folder_path", ""))
            file_path = dist_dir / "assets" / filename
            if file_path.exists():
                return send_from_directory(str(dist_dir / "assets"), filename)

    # Search in all feature dist/assets directories
    features_dir = Path("/app/features")
    if not features_dir.exists():
        features_dir = Path(__file__).resolve().parent.parent.parent / "features"

    for feat_dir in features_dir.glob("*/dist/assets"):
        target_file = feat_dir / filename
        if target_file.exists():
            return send_from_directory(str(feat_dir), filename)

    abort(404)


# ─── Helper ───────────────────────────────────────────────────────────────────

def _extract_optional_user_id() -> str | None:
    """
    Try to extract the authenticated user's ID from the Authorization header
    without requiring it. Returns None if no valid token is present.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header:
        return None
    parts = auth_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    try:
        payload = decode_jwt_token(parts[1])
        email = payload.get("email")
        if not email:
            return None
        user = find_user_by_email(email)
        return str(user["_id"]) if user else None
    except Exception:
        return None
