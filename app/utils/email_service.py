import logging
import sys

logger = logging.getLogger("email_service")


def send_verification_email(email: str, code: str) -> bool:
    """
    Sends a verification code to the specified email address.
    During development/testing, outputs the OTP prominently in console logs.
    Can be hooked into standard SMTP, SendGrid, Amazon SES, etc.
    """
    banner = f"""
============================================================
 [EMAIL SERVICE] VERIFICATION CODE DISPATCHED
 To:   {email}
 Code: {code}
 (Valid for 10 minutes)
============================================================
"""
    # Print directly to stdout and log so it's visible in both container and app logs
    print(banner, flush=True)
    logger.info(f"Verification code '{code}' sent to {email}")
    return True
