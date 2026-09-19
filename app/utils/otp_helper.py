import secrets
import string


def generate_otp_code(length: int = 6) -> str:
    """
    Generates a secure numeric OTP of the specified length.
    """
    digits = string.digits
    return "".join(secrets.choice(digits) for _ in range(length))


def generate_temp_token() -> str:
    """
    Generates a cryptographically secure random temporary token.
    Used for Stage 1 verification flow.
    """
    return secrets.token_urlsafe(32)
