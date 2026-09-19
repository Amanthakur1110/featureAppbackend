from flask import Blueprint
from app.controllers.auth_controller import (
    login_request_controller,
    verify_code_controller,
    get_me_controller
)
from app.middlewares.auth_middleware import token_required

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Stage 1: Request verification code by email (creates account if new)
# Matches Android ApiServer.authApi.login(loginViaEmail)
auth_bp.route("/login", methods=["POST"])(login_request_controller)

# Stage 2: Submit email + token + OTP code to receive real JWT session token
auth_bp.route("/verify-code", methods=["POST"])(verify_code_controller)

# Protected route: Retrieve current authenticated user session
# Requires Authorization: Bearer <jwt_token>
@auth_bp.route("/me", methods=["GET"])
@token_required
def get_me():
    return get_me_controller()
