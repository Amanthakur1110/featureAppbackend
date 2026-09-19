import re

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def is_valid_email(email: str) -> bool:
    """Validates email format."""
    if not email or not isinstance(email, str):
        return False
    return bool(EMAIL_REGEX.match(email.strip()))


def validate_required_fields(data: dict, required_fields: list) -> tuple[bool, str]:
    """
    Validates that all required fields are present and non-empty in data dictionary.
    Returns (True, "") if valid, or (False, "Field 'xyz' is required") if invalid.
    """
    if not isinstance(data, dict):
        return False, "Request body must be valid JSON object"

    for field in required_fields:
        val = data.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            return False, f"Field '{field}' is required and cannot be empty"

    return True, ""
