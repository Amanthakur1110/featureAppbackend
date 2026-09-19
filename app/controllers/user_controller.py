from flask import request, g
from app.utils.response import success_response, error_response
from app.models.user_model import update_user_profile


def get_user_profile_controller():
    """Returns the profile of the currently authenticated user."""
    current_user = getattr(g, "current_user", None)
    if not current_user:
        return error_response(message="User not authenticated", status_code=401)

    return success_response(
        data=current_user,
        message="User profile retrieved successfully",
        status_code=200
    )


def update_user_profile_controller():
    """Updates profile fields (e.g. display_name, bio) for the authenticated user."""
    current_user = getattr(g, "current_user", None)
    if not current_user:
        return error_response(message="User not authenticated", status_code=401)

    payload = request.get_json(silent=True) or {}
    allowed_fields = ["display_name", "bio"]
    updates = {k: v for k, v in payload.items() if k in allowed_fields}

    if not updates:
        return error_response(
            message="No valid fields to update. Allowed: display_name, bio",
            status_code=400
        )

    updated_user = update_user_profile(current_user["email"], updates)
    return success_response(
        data=updated_user,
        message="Profile updated successfully",
        status_code=200
    )
