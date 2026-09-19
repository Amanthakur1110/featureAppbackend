from flask import Blueprint
from app.controllers.user_controller import (
    get_user_profile_controller,
    update_user_profile_controller
)
from app.middlewares.auth_middleware import token_required

user_bp = Blueprint("user", __name__, url_prefix="/user")

# Protected user profile routes (Requires Authorization: Bearer <jwt_token>)
@user_bp.route("/profile", methods=["GET"])
@token_required
def get_profile():
    return get_user_profile_controller()


@user_bp.route("/profile", methods=["PUT"])
@token_required
def update_profile():
    return update_user_profile_controller()
