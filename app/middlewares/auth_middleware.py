from functools import wraps
from flask import request, g
import jwt

from app.utils.jwt_helper import decode_jwt_token
from app.utils.response import error_response
from app.models.user_model import find_user_by_email, serialize_user


def token_required(f):
    """
    Middleware / Decorator to protect routes requiring JWT authentication.
    Expects 'Authorization: Bearer <token>' in request headers.
    Attaches the authenticated user dict to `flask.g.current_user`.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        
        if not auth_header:
            return error_response(
                message="Authorization header is missing",
                status_code=401
            )

        parts = auth_header.strip().split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return error_response(
                message="Invalid Authorization header format. Expected 'Bearer <token>'",
                status_code=401
            )

        token = parts[1]

        try:
            payload = decode_jwt_token(token)
        except jwt.ExpiredSignatureError:
            return error_response(
                message="Session token has expired. Please log in again.",
                status_code=401
            )
        except jwt.InvalidTokenError as e:
            return error_response(
                message=f"Invalid authentication token: {str(e)}",
                status_code=401
            )
        except Exception as e:
            return error_response(
                message="Token verification failed",
                status_code=401
            )

        email = payload.get("email")
        if not email:
            return error_response(
                message="Token payload is missing user identity",
                status_code=401
            )

        user = find_user_by_email(email)
        if not user:
            return error_response(
                message="User associated with this token no longer exists",
                status_code=401
            )

        if not user.get("is_active", True):
            return error_response(
                message="User account is deactivated",
                status_code=403
            )

        # Attach clean user dict to request context
        g.current_user = serialize_user(user)

        return f(*args, **kwargs)

    return decorated
