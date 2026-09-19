#!/usr/bin/env python3
"""
Automated Test Suite for FeatureApp Backend.
Validates:
1. System health check (/api/v1/health)
2. Stage 1 Login / Signup flow (/api/v1/auth/login)
3. Stage 2 Code verification with OTP (/api/v1/auth/verify-code)
4. OTP Replay protection (prevent reusing code)
5. Protected route security without token (/api/v1/auth/me) -> 401
6. Protected route access with JWT Bearer token -> 200
7. User profile update (/api/v1/user/profile) -> 200
"""

import sys
import os
import time
import requests
from pymongo import MongoClient

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8004/api/v1")
MONGO_URI = os.getenv("TEST_MONGO_URI") or os.getenv("MONGO_URI", "mongodb://localhost:27017/featureapp")


def print_test_header(title):
    print(f"\n---> [TEST] {title}")


def assert_true(condition, message):
    if not condition:
        print(f"  ❌ FAILED: {message}")
        sys.exit(1)
    else:
        print(f"  ✅ PASSED: {message}")


def run_tests():
    print("=" * 60)
    print(f" Starting FeatureApp Backend Automated Verification Suite")
    print(f" Target Base URL: {BASE_URL}")
    print("=" * 60)

    # 1. Health Check
    print_test_header("1. Health Check Endpoint (/api/v1/health)")
    try:
        res = requests.get(f"{BASE_URL}/health", timeout=5)
        assert_true(res.status_code == 200, f"Status code is 200 (got {res.status_code})")
        data = res.json()
        assert_true(data.get("success") is True, "Response success is True")
        assert_true(data.get("data", {}).get("database", {}).get("healthy") is True, "Database is healthy")
    except requests.exceptions.ConnectionError:
        print(f"  ❌ Could not connect to backend at {BASE_URL}. Ensure server or container is running.")
        sys.exit(1)

    # 2. Stage 1: Validation Failures
    print_test_header("2. Stage 1 Input Validation")
    # Empty body
    res = requests.post(f"{BASE_URL}/auth/login", json={})
    assert_true(res.status_code == 400, f"Empty body rejected with 400 (got {res.status_code})")

    # Invalid email
    res = requests.post(f"{BASE_URL}/auth/login", json={"email": "invalid_email_format"})
    assert_true(res.status_code == 400, f"Malformed email rejected with 400 (got {res.status_code})")

    # 3. Stage 1: Valid Email Submission
    print_test_header("3. Stage 1 Request Code (/api/v1/auth/login)")
    test_email = f"test_user_{int(time.time())}@example.com"
    payload = {"email": test_email}
    res = requests.post(f"{BASE_URL}/auth/login", json=payload)
    assert_true(res.status_code == 200, f"Status code is 200 (got {res.status_code})")
    body = res.json()
    assert_true(body.get("success") is True, "Envelope success is True")
    token_data = body.get("data", {})
    temp_token = token_data.get("token")
    assert_true(bool(temp_token), f"Temporary token generated: {temp_token[:10]}...")
    assert_true(token_data.get("email") == test_email, "Returned email matches request")

    # Connect to MongoDB to fetch the generated OTP code for automated testing
    print_test_header("4. Fetch OTP Code from Database for Verification")
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    db = mongo_client["featureapp"]
    otp_doc = db.verification_tokens.find_one({"email": test_email, "token": temp_token})
    assert_true(otp_doc is not None, "Verification session document found in MongoDB")
    correct_otp = otp_doc.get("code")
    assert_true(len(correct_otp) == 6, f"OTP code is 6 digits: {correct_otp}")

    # 5. Stage 2: Verification with Incorrect Code
    print_test_header("5. Stage 2 Code Verification Rejection (/api/v1/auth/verify-code)")
    wrong_payload = {
        "email": test_email,
        "token": temp_token,
        "code": "000000" if correct_otp != "000000" else "999999"
    }
    res = requests.post(f"{BASE_URL}/auth/verify-code", json=wrong_payload)
    assert_true(res.status_code == 400, f"Wrong code rejected with 400 (got {res.status_code})")

    # 6. Stage 2: Verification with Correct Code
    print_test_header("6. Stage 2 Code Verification Success (/api/v1/auth/verify-code)")
    correct_payload = {
        "email": test_email,
        "token": temp_token,
        "code": correct_otp
    }
    res = requests.post(f"{BASE_URL}/auth/verify-code", json=correct_payload)
    assert_true(res.status_code == 200, f"Status code is 200 (got {res.status_code})")
    body = res.json()
    assert_true(body.get("success") is True, "Response success is True")
    jwt_token = body.get("data", {}).get("token")
    assert_true(bool(jwt_token), f"Received JWT Session Token: {jwt_token[:15]}...")

    # 7. Replay Attack Prevention
    print_test_header("7. Verification Replay Attack Protection")
    res = requests.post(f"{BASE_URL}/auth/verify-code", json=correct_payload)
    assert_true(res.status_code == 400, f"Reused verification token rejected with 400 (got {res.status_code})")

    # 8. Protected Route: Without Token
    print_test_header("8. Protected Route Without Token (/api/v1/auth/me)")
    res = requests.get(f"{BASE_URL}/auth/me")
    assert_true(res.status_code == 401, f"Access rejected with 401 (got {res.status_code})")

    # 9. Protected Route: With Invalid Bearer Token
    print_test_header("9. Protected Route With Bogus Bearer Token")
    headers = {"Authorization": "Bearer invalid.jwt.signature"}
    res = requests.get(f"{BASE_URL}/auth/me", headers=headers)
    assert_true(res.status_code == 401, f"Access rejected with 401 (got {res.status_code})")

    # 10. Protected Route: With Valid JWT Token
    print_test_header("10. Protected Route With Valid JWT Token (/api/v1/auth/me)")
    headers = {"Authorization": f"Bearer {jwt_token}"}
    res = requests.get(f"{BASE_URL}/auth/me", headers=headers)
    assert_true(res.status_code == 200, f"Access granted with 200 (got {res.status_code})")
    user_data = res.json().get("data", {})
    assert_true(user_data.get("email") == test_email, f"Returned user email matches: {user_data.get('email')}")

    # 11. Protected Feature Route: User Profile
    print_test_header("11. Protected User Profile (/api/v1/user/profile)")
    res = requests.get(f"{BASE_URL}/user/profile", headers=headers)
    assert_true(res.status_code == 200, f"Profile GET returned 200 (got {res.status_code})")

    # Update Profile
    update_payload = {"display_name": "Aman Dev", "bio": "Senior Android Engineer"}
    res = requests.put(f"{BASE_URL}/user/profile", json=update_payload, headers=headers)
    assert_true(res.status_code == 200, f"Profile PUT returned 200 (got {res.status_code})")
    updated_profile = res.json().get("data", {}).get("profile", {})
    assert_true(updated_profile.get("display_name") == "Aman Dev", "Profile display_name updated")

    # 12. Superseded Session Test (Old code cannot be used once a new code is requested)
    print_test_header("12. Superseded Token Invalidation")
    multi_email = f"supersede_test_{int(time.time())}@example.com"
    # Request code 1
    res1 = requests.post(f"{BASE_URL}/auth/login", json={"email": multi_email})
    token1 = res1.json().get("data", {}).get("token")
    otp1 = db.verification_tokens.find_one({"email": multi_email, "token": token1})["code"]
    
    # Request code 2 (supersedes token1)
    res2 = requests.post(f"{BASE_URL}/auth/login", json={"email": multi_email})
    token2 = res2.json().get("data", {}).get("token")
    otp2 = db.verification_tokens.find_one({"email": multi_email, "token": token2})["code"]

    # Try verifying token1 (should fail because it was superseded)
    res_old = requests.post(f"{BASE_URL}/auth/verify-code", json={"email": multi_email, "token": token1, "code": otp1})
    assert_true(res_old.status_code == 400, f"Old superseded session rejected with 400 (got {res_old.status_code})")
    assert_true("no longer valid" in res_old.json().get("message", ""), "Error message indicates session is no longer valid")

    # Verify token2 (should succeed)
    res_new = requests.post(f"{BASE_URL}/auth/verify-code", json={"email": multi_email, "token": token2, "code": otp2})
    assert_true(res_new.status_code == 200, f"Latest session verified with 200 (got {res_new.status_code})")

    # 13. Brute Force Lockout Test (5 failed attempts locks session)
    print_test_header("13. Brute Force Attempt Lockout")
    lock_email = f"lock_test_{int(time.time())}@example.com"
    res_lock = requests.post(f"{BASE_URL}/auth/login", json={"email": lock_email})
    lock_token = res_lock.json().get("data", {}).get("token")
    correct_lock_otp = db.verification_tokens.find_one({"email": lock_email, "token": lock_token})["code"]

    # Send 5 incorrect attempts
    for i in range(1, 6):
        res_fail = requests.post(f"{BASE_URL}/auth/verify-code", json={"email": lock_email, "token": lock_token, "code": "000000"})
        assert_true(res_fail.status_code == 400, f"Attempt {i} rejected with 400")

    # 6th attempt with CORRECT OTP should now be rejected because the session is locked
    res_locked = requests.post(f"{BASE_URL}/auth/verify-code", json={"email": lock_email, "token": lock_token, "code": correct_lock_otp})
    assert_true(res_locked.status_code == 400, f"Locked session rejected even with correct OTP (got {res_locked.status_code})")

    print("\n" + "=" * 60)
    print(" 🎉 ALL 13 TESTS PASSED SUCCESSFULLY! ")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
