from datetime import datetime, timezone, timedelta
import jwt
from flask import current_app


def generate_jwt_token(payload: dict, expires_in_days: int = None) -> str:
    """
    Encodes payload with expiration into a signed JWT string.
    """
    secret_key = current_app.config.get("JWT_SECRET_KEY")
    if expires_in_days is None:
        expires_in_days = current_app.config.get("JWT_ACCESS_TOKEN_EXPIRES_DAYS", 30)

    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=expires_in_days)

    token_payload = payload.copy()
    token_payload.update({
        "iat": now,
        "exp": exp
    })

    token = jwt.encode(token_payload, secret_key, algorithm="HS256")
    return token


def decode_jwt_token(token: str) -> dict:
    """
    Decodes and validates a JWT token string.
    Raises jwt.ExpiredSignatureError or jwt.InvalidTokenError on failure.
    """
    secret_key = current_app.config.get("JWT_SECRET_KEY")
    decoded = jwt.decode(token, secret_key, algorithms=["HS256"])
    return decoded
