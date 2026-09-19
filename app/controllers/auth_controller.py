from datetime import datetime, timezone
from flask import request, g, current_app

from app.utils.response import success_response, error_response
from app.utils.validators import is_valid_email, validate_required_fields
from app.utils.otp_helper import generate_otp_code, generate_temp_token
from app.utils.jwt_helper import generate_jwt_token
from app.utils.email_service import send_verification_email
from app.models.user_model import (
    get_or_create_user,
    find_user_by_email,
    update_last_login,
    serialize_user
)
from app.models.otp_model import (
    create_verification_session,
    find_verification_session,
    mark_session_used,
    record_failed_attempt
)


def login_request_controller():
    """
    Stage 1: User enters email.
    Creates account if not exists, generates temporary token + OTP code,
    sends code to user, and returns Response<loginViaEmailResponse>.
    """
    payload = request.get_json(silent=True) or {}
    
    is_valid, err_msg = validate_required_fields(payload, ["email"])
    if not is_valid:
        return error_response(message=err_msg, status_code=400)

    email = payload.get("email", "").strip().lower()
    if not is_valid_email(email):
        return error_response(message="Invalid email address format", status_code=400)

    # Unified Login / Signup: get or create user account
    user, is_new = get_or_create_user(email)

    if not user.get("is_active", True):
        return error_response(message="This account is deactivated. Please contact support.", status_code=403)

    # Generate 6-digit OTP code & temporary verification token
    otp_code = generate_otp_code(6)
    temp_token = generate_temp_token()
    expiry_minutes = current_app.config.get("OTP_EXPIRY_MINUTES", 10)

    # Persist verification session in database
    create_verification_session(
        email=email,
        token=temp_token,
        code=otp_code,
        expires_in_minutes=expiry_minutes
    )

    # Send verification email / log to console
    send_verification_email(email, otp_code)

    action_text = "Account created and verification code sent" if is_new else "Verification code sent to your email"

    # Shape data to match Android loginViaEmailResponse:
    # data: { message: String?, token: String?, email: String? }
    response_data = {
        "message": f"Verification code sent to {email}",
        "token": temp_token,
        "email": email
    }

    return success_response(
        data=response_data,
        message=action_text,
        status_code=200
    )


def verify_code_controller():
    """
    Stage 2: User provides email, temp token, and verification code.
    Verifies code against stored session.
    If valid, issues real JWT session token and returns Response<loginViaEmailResponse>.
    """
    payload = request.get_json(silent=True) or {}

    is_valid, err_msg = validate_required_fields(payload, ["email", "token", "code"])
    if not is_valid:
        return error_response(message=err_msg, status_code=400)

    email = payload.get("email", "").strip().lower()
    token = payload.get("token", "").strip()
    code = str(payload.get("code", "")).strip()

    if not is_valid_email(email):
        return error_response(message="Invalid email address format", status_code=400)

    # Retrieve verification session from DB
    session = find_verification_session(email=email, token=token)
    if not session:
        return error_response(
            message="Invalid or unrecognized verification session. Please request a new code.",
            status_code=400
        )

    # Check if already used
    if session.get("used", False):
        status = session.get("status", "used")
        if status == "superseded":
            msg = "A newer verification code was requested. This code is no longer valid."
        elif status == "exceeded_max_attempts":
            msg = "This verification session was locked due to too many failed attempts. Please request a new code."
        else:
            msg = "This verification code has already been used and is no longer valid. Please request a new code."
        return error_response(message=msg, status_code=400)

    # Check expiration
    expires_at = session.get("expires_at")
    if expires_at:
        # Handle naive vs aware datetime comparison
        now = datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            return error_response(
                message="Verification code has expired. Please request a new code.",
                status_code=400
            )

    # Check code match with failed attempt tracking
    if session.get("code") != code:
        attempts, is_locked = record_failed_attempt(session["_id"])
        if is_locked:
            return error_response(
                message="Too many incorrect attempts. This verification code has been permanently locked. Please request a new code.",
                status_code=400
            )
        remaining = max(0, 5 - attempts)
        return error_response(
            message=f"Incorrect verification code. {remaining} attempt(s) remaining.",
            status_code=400
        )

    # Atomically mark session as used so it cannot be used again
    was_marked = mark_session_used(session["_id"])
    if not was_marked:
        return error_response(
            message="This verification code has already been used. Please request a new code.",
            status_code=400
        )

    # Look up user & update last login
    user = find_user_by_email(email)
    if not user:
        # Failsafe in case user was deleted between stage 1 and 2
        user, _ = get_or_create_user(email)

    update_last_login(email)

    # Generate real JWT session token
    jwt_token = generate_jwt_token({
        "email": email,
        "sub": str(user["_id"])
    })

    response_data = {
        "message": "Authentication successful",
        "token": jwt_token,
        "email": email
    }

    return success_response(
        data=response_data,
        message="Login successful",
        status_code=200
    )


def get_me_controller():
    """
    Protected route: Returns the profile data of the currently logged-in user.
    `g.current_user` is populated by @token_required middleware.
    """
    current_user = getattr(g, "current_user", None)
    if not current_user:
        return error_response(message="User context not found", status_code=401)

    return success_response(
        data=current_user,
        message="Profile retrieved successfully",
        status_code=200
    )
