# FeatureApp Backend

A professional, modular, Docker-based Flask backend with MongoDB, JWT Bearer authentication, and a two-stage passwordless login flow. Built specifically to power the **FeatureApp** Android application.

---

## Architecture & Directory Structure

```
featureAppBackend/
├── app/
│   ├── __init__.py                # App factory (create_app), registers blueprints & error handlers
│   ├── config.py                  # Configurations (Dev, Prod, Test) from environment variables
│   ├── db/
│   │   ├── __init__.py
│   │   └── mongo.py               # PyMongo connection manager, health check & TTL index setup
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user_model.py          # User collection schema & CRUD operations
│   │   └── otp_model.py           # Verification tokens & OTP session collection operations
│   ├── middlewares/
│   │   ├── __init__.py
│   │   └── auth_middleware.py     # @token_required decorator for Bearer JWT validation
│   ├── controllers/
│   │   ├── __init__.py
│   │   ├── auth_controller.py     # Stage 1 login, Stage 2 code verification, user identity
│   │   └── user_controller.py     # User profile operations
│   ├── routes/
│   │   ├── __init__.py            # Central blueprint registry under /api/v1 prefix
│   │   ├── auth_routes.py         # /api/v1/auth/login, /api/v1/auth/verify-code, /api/v1/auth/me
│   │   └── user_routes.py         # /api/v1/user/profile (protected with @token_required)
│   └── utils/
│       ├── __init__.py
│       ├── response.py            # success_response, error_response (matching Android Response<T>)
│       ├── jwt_helper.py          # PyJWT encoding & decoding helpers
│       ├── otp_helper.py          # OTP 6-digit generator & temporary token generator
│       ├── email_service.py       # Console OTP logger & extensible email sender
│       └── validators.py          # Request body & email validators
├── .dockerignore
├── .env.example
├── .env
├── .gitignore
├── Dockerfile                     # Python 3.12 Dockerfile
├── docker-compose.yml             # Flask app on 8004:8004 + MongoDB on 27017:27017
├── requirements.txt               # Dependencies
├── run.py                         # Development entrypoint
├── wsgi.py                        # Production WSGI entrypoint
├── test_api.py                    # Automated test verification suite
└── README.md                      # Documentation
```

---

## Two-Stage Passwordless Auth Flow

This backend implements the two-stage passwordless login requested for `FeatureApp`:

### Stage 1: Request Verification Code
- **Endpoint**: `POST /api/v1/auth/login`
- **Request Body**:
  ```json
  {
    "email": "user@example.com"
  }
  ```
- **Process**:
  1. Creates user account in MongoDB if it does not exist (unified signup/login).
  2. Generates a temporary verification token (unique 64-char string) and a 6-digit OTP.
  3. Saves the session with 10-minute expiration in MongoDB.
  4. Dispatches the OTP code via email service (printed clearly in backend console during dev).
- **Response** (Matches Android `Response<loginViaEmailResponse>`):
  ```json
  {
    "success": true,
    "message": "Verification code sent to your email",
    "data": {
      "message": "Verification code sent to user@example.com. Valid for 10 minutes.",
      "token": "dGhpcy1pcy1hLXRlbXBvcmFyeS10b2tlbg...",
      "email": "user@example.com"
    }
  }
  ```

### Stage 2: Code Verification & JWT Issuance
- **Endpoint**: `POST /api/v1/auth/verify-code`
- **Request Body**:
  ```json
  {
    "email": "user@example.com",
    "token": "dGhpcy1pcy1hLXRlbXBvcmFyeS10b2tlbg...",
    "code": "123456"
  }
  ```
- **Process**:
  1. Validates that the token belongs to the given email.
  2. Ensures code has not expired and has not been used.
  3. Verifies that the entered OTP matches the stored code.
  4. Marks the session as `used = True` (preventing replay attacks).
  5. Updates user `last_login` timestamp.
  6. Generates a signed **JWT session token** (valid for 30 days).
- **Response**:
  ```json
  {
    "success": true,
    "message": "Login successful",
    "data": {
      "message": "Authentication successful",
      "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
      "email": "user@example.com"
    }
  }
  ```

### Stage 3: Protected Requests
- Clients send the JWT token in standard Bearer format:
  ```http
  Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
  ```
- Routes decorated with `@token_required` automatically decode the JWT, verify the user in MongoDB, and populate `flask.g.current_user`.

---

## Quickstart with Docker Compose

### 1. Start Services
From the `featureAppBackend` directory:
```bash
docker compose up -d --build
```

### 2. Check Service Status
```bash
docker compose ps
```
Both `featureapp-backend` and `featureapp-mongo` should be in running/healthy state.

### 3. View Logs (To see OTP codes in real time)
```bash
docker compose logs -f backend
```

### 4. Stop Services
```bash
docker compose down
```

---

## How to Add New Routes & Controllers

Adding a new feature to the backend is completely modular and requires only two steps:

### Step 1: Create a Controller
Create `app/controllers/product_controller.py`:
```python
from app.utils.response import success_response

def list_products_controller():
    # Your business logic here
    products = [{"id": 1, "name": "Feature 1"}]
    return success_response(data=products, message="Products retrieved")
```

### Step 2: Create Route File & Register
Create `app/routes/product_routes.py`:
```python
from flask import Blueprint
from app.controllers.product_controller import list_products_controller
from app.middlewares.auth_middleware import token_required

product_bp = Blueprint("product", __name__, url_prefix="/products")

# Public route
@product_bp.route("/", methods=["GET"])
def get_products():
    return list_products_controller()

# Protected route
@product_bp.route("/protected", methods=["POST"])
@token_required
def create_product():
    # Only authenticated users can access
    ...
```

Register it in `app/routes/__init__.py`:
```python
from app.routes.product_routes import product_bp

ALL_BLUEPRINTS = [
    auth_bp,
    user_bp,
    product_bp, # <-- Add here! It is automatically mapped to /api/v1/products
]
```

---

## Connecting with Android FeatureApp

The Android client (`AppConstant.kt`) is pre-configured with:
```kotlin
object AppConstant {
    object Network {
        val BaseUrl = "http://localhost:8004/api/v1/"
    }
}
```

### Android Emulator Setup
When running an Android Emulator, map the emulator's port to host machine:
```bash
adb reverse tcp:8004 tcp:8004
```
This allows the Android app running in the emulator to communicate directly with `http://localhost:8004/api/v1/` without modifying Android source code.

---

## Testing

Run the automated test suite against the running backend:
```bash
python3 test_api.py
```
This validates health, Stage 1 login, Stage 2 verification, replay rejection, and JWT Bearer protected routes.
