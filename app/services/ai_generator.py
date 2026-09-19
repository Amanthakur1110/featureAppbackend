"""
AI Feature Generator — Vite + React 18 + Tailwind CSS Persistent Workspaces.

Runs feature code generation in a background thread so the HTTP response
returns immediately with status 'building'. The thread updates the feature
status in MongoDB as it progresses.

## Thread Lifecycle
  1. generate_feature_async(uid, ...) — atomically registers a new generation_id
     and spawns a worker thread.
  2. Sets up or reuses persistent workspace at /app/workspaces/<uid>.
  3. Worker calls Gemini Generative Language API with React + Tailwind + Bridge instructions.
     If editing, current src/App.jsx code is passed into prompt.
  4. A lightweight heartbeat thread runs in parallel, writing 'building' to DB
     every 15 s so the Android poller knows the worker is still alive.
  5. If Gemini returns needs_clarification → status = 'clarifying', worker pauses.
  6. User answers → continue_after_clarification() resumes the worker.
  7. Gemini returns JSX → written to src/App.jsx.
  8. Vite builds production bundle (`npm run build`).
  9. Static files from dist/ copied to feature folder_path → status = 'ready'.
  10. Any unhandled exception → status = 'error'.
"""

import os
import json
import logging
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.db.mongo import get_db
from app.models.feature_model import (
    find_feature_by_uid,
    update_feature_status,
    update_feature_fields,
    append_chat_message,
    STATUS_BUILDING,
    STATUS_CLARIFYING,
    STATUS_READY,
    STATUS_ERROR,
)
from app.services.system_prompt import build_system_prompt

import requests as http_requests

logger = logging.getLogger("ai_generator")


def _get_template_dir() -> Path:
    env_dir = os.getenv("TEMPLATE_DIR")
    if env_dir:
        return Path(env_dir)
    if Path("/app/template_vite_react").exists():
        return Path("/app/template_vite_react")
    return Path(__file__).resolve().parent.parent.parent / "template_vite_react"


def _get_workspace_dir() -> Path:
    env_dir = os.getenv("WORKSPACE_BASE_DIR")
    if env_dir:
        return Path(env_dir)
    if Path("/app").exists() and Path("/app/template_vite_react").exists():
        return Path("/app/workspaces")
    return Path(__file__).resolve().parent.parent.parent / "workspaces"


# ─── Per-generation State ─────────────────────────────────────────────────────
_lock = threading.Lock()

# uid → current generation_id (int counter, increments on each new generation)
_generation_ids: dict[str, int] = {}

# generation_id → threading.Event (for clarification wake-up)
_clarification_events: dict[int, threading.Event] = {}

# generation_id → user's clarification answer string
_clarification_answers: dict[int, str] = {}

# Set of generation_ids that have been cancelled
_cancelled_gen_ids: set[int] = set()

# Global monotonic counter for generation_ids
_gen_id_counter = 0


# ─── Public API ───────────────────────────────────────────────────────────────

def generate_feature_async(uid: str, name: str, description: str, folder_path: str, app) -> None:
    """
    Spawn a background thread to generate the feature.
    Atomically assigns a new generation_id so that any previous cancellation
    for this uid does NOT affect the new thread.
    """
    global _gen_id_counter

    with _lock:
        _gen_id_counter += 1
        gen_id = _gen_id_counter
        _generation_ids[uid] = gen_id
        event = threading.Event()
        _clarification_events[gen_id] = event

    thread = threading.Thread(
        target=_generation_worker,
        args=(uid, gen_id, name, description, folder_path, app),
        daemon=True,
        name=f"gen-{uid[:8]}-v{gen_id}"
    )
    thread.start()
    logger.info(f"Started generation thread for feature {uid} (gen_id={gen_id})")


def continue_after_clarification(uid: str, user_answer: str) -> bool:
    """
    Called by the controller when the user sends a clarification answer.
    Wakes the paused generation thread and passes the answer.
    Returns False if no active thread is waiting for this uid.
    """
    with _lock:
        gen_id = _generation_ids.get(uid)
        if gen_id is None:
            return False
        event = _clarification_events.get(gen_id)
        if event is None:
            return False
        _clarification_answers[gen_id] = user_answer

    event.set()
    logger.info(f"Clarification answer received for feature {uid} (gen_id={gen_id})")
    return True


def cancel_generation(uid: str) -> None:
    """
    Cancel the current generation thread for `uid` (if any).
    Marks only the current generation_id as cancelled.
    """
    with _lock:
        gen_id = _generation_ids.pop(uid, None)
        if gen_id is not None:
            _cancelled_gen_ids.add(gen_id)
            event = _clarification_events.get(gen_id)
        else:
            event = None

    if event:
        event.set()  # Unblock any waiting clarification event.wait()
    logger.info(
        f"Cancellation requested for feature {uid} (gen_id={gen_id})"
        if gen_id else f"No active generation found for feature {uid}"
    )


def is_cancelled(gen_id: int) -> bool:
    """Thread-safe check whether a specific generation has been cancelled."""
    with _lock:
        return gen_id in _cancelled_gen_ids


# ─── Internal Worker ──────────────────────────────────────────────────────────

def _generation_worker(uid: str, gen_id: int, name: str, description: str,
                        folder_path: str, app) -> None:
    """
    Background thread: drives the full generation lifecycle.
    Uses the Flask app context so DB calls work inside the thread.
    """
    with app.app_context():
        if is_cancelled(gen_id):
            logger.info(f"Gen {gen_id} ({uid}): cancelled before start, exiting")
            _cleanup(gen_id)
            return

        heartbeat = _HeartbeatThread(uid, gen_id)
        heartbeat.start()

        try:
            _run_generation(uid, gen_id, name, description, folder_path)
        except Exception as e:
            logger.error(f"Gen {gen_id} ({uid}): unhandled error: {e}", exc_info=True)
            if not is_cancelled(gen_id):
                update_feature_status(uid, STATUS_ERROR, error_msg=str(e))
        finally:
            heartbeat.stop()
            _cleanup(gen_id)


def _cleanup(gen_id: int) -> None:
    """Remove all registry entries for a finished/cancelled generation."""
    with _lock:
        _clarification_events.pop(gen_id, None)
        _clarification_answers.pop(gen_id, None)
        _cancelled_gen_ids.discard(gen_id)


# ─── Heartbeat ────────────────────────────────────────────────────────────────

class _HeartbeatThread(threading.Thread):
    """
    Writes a 'building' heartbeat to MongoDB every INTERVAL seconds while the
    main generation thread is working.
    """
    INTERVAL = 15  # seconds between heartbeats

    def __init__(self, uid: str, gen_id: int):
        super().__init__(daemon=True, name=f"hb-{uid[:8]}-v{gen_id}")
        self.uid = uid
        self.gen_id = gen_id
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.wait(timeout=self.INTERVAL):
            if is_cancelled(self.gen_id):
                break
            try:
                db = get_db()
                db.features.update_one(
                    {"uid": self.uid, "status": STATUS_BUILDING},
                    {"$set": {"updated_at": datetime.now(timezone.utc)}}
                )
                logger.debug(f"Heartbeat written for gen {self.gen_id} ({self.uid})")
            except Exception as e:
                logger.warning(f"Heartbeat write failed for {self.uid}: {e}")

    def stop(self):
        self._stop_event.set()


# ─── Workspace Management ─────────────────────────────────────────────────────

def _setup_workspace(uid: str) -> Path:
    """
    Initializes or retrieves the persistent workspace for this feature.
    Reuses template files and symlinks node_modules for zero-overhead builds.
    """
    ws = _get_workspace_dir() / uid
    ws.mkdir(parents=True, exist_ok=True)
    template_path = _get_template_dir()

    # Base configuration files to copy if missing
    config_files = [
        "package.json",
        "vite.config.js",
        "index.html",
        "tailwind.config.js",
        "postcss.config.js",
    ]
    for fname in config_files:
        src = template_path / fname
        dst = ws / fname
        if not dst.exists() and src.exists():
            shutil.copy2(src, dst)

    # Ensure src directory with main.jsx and index.css
    src_dir = ws / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    for fname in ["main.jsx", "index.css"]:
        src = template_path / "src" / fname
        dst = src_dir / fname
        if not dst.exists() and src.exists():
            shutil.copy2(src, dst)

    # Symlink node_modules from template to workspace for instant builds
    ws_node_modules = ws / "node_modules"
    template_node_modules = template_path / "node_modules"
    if not ws_node_modules.exists() and template_node_modules.exists():
        try:
            ws_node_modules.symlink_to(template_node_modules)
            logger.info(f"Workspace {uid}: symlinked node_modules from {template_node_modules}")
        except Exception as e:
            logger.warning(f"Workspace {uid}: could not symlink node_modules: {e}")

    return ws


def _get_existing_code(ws: Path) -> str | None:
    """
    Returns existing App.jsx code if present in the workspace.
    """
    app_jsx = ws / "src" / "App.jsx"
    if app_jsx.exists():
        try:
            content = app_jsx.read_text(encoding="utf-8").strip()
            if len(content) > 30:
                return content
        except Exception as e:
            logger.warning(f"Could not read existing App.jsx in {ws}: {e}")
    return None


def _build_workspace(ws: Path, gen_id: int, uid: str) -> tuple[bool, str]:
    """
    Executes `npm run build` in the workspace.
    Returns (True, "") on success, or (False, error_details) on failure.
    """
    try:
        res = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if res.returncode == 0 and (ws / "dist" / "index.html").exists():
            logger.info(f"Gen {gen_id} ({uid}): Vite build succeeded")
            return True, ""

        err_msg = res.stderr.strip() or res.stdout.strip()
        logger.warning(f"Gen {gen_id} ({uid}): Vite build failed (rc={res.returncode}):\n{err_msg}")
        return False, err_msg
    except subprocess.TimeoutExpired:
        logger.error(f"Gen {gen_id} ({uid}): Vite build timed out after 60s")
        return False, "Vite build timed out after 60 seconds"
    except Exception as e:
        logger.error(f"Gen {gen_id} ({uid}): error running npm run build: {e}")
        return False, str(e)


def _patch_index_html_for_webview(index_html_path: Path, uid: str) -> None:
    """
    Post-process the Vite-built index.html to ensure compatibility with Android WebView.

    Vite always emits:
      <script type="module" crossorigin src="...">
      <link rel="stylesheet" crossorigin href="...">

    Android WebView CORS-blocks crossorigin module scripts when loaded over HTTP
    from a remote server (10.x.x.x:8004). Fixes:
    1. Remove 'crossorigin' attribute from <script> and <link> tags.
    2. Change 'type="module"' to 'defer' on script tags so the IIFE runs after DOM load.
    """
    try:
        content = index_html_path.read_text(encoding="utf-8")
        import re

        # Replace: <script type="module" crossorigin src="...">
        # With:    <script defer src="...">
        content = re.sub(
            r'<script\s+type=["\']module["\']\s+crossorigin\s+src=',
            '<script defer src=',
            content
        )
        # Also handle reversed attribute order: crossorigin type="module"
        content = re.sub(
            r'<script\s+crossorigin\s+type=["\']module["\']\s+src=',
            '<script defer src=',
            content
        )
        # Remove crossorigin from remaining <script> tags
        content = re.sub(r'(<script\b[^>]*?)\s+crossorigin(\s|>)', r'\1\2', content)

        # Remove crossorigin from <link> tags (stylesheets)
        content = re.sub(r'(<link\b[^>]*?)\s+crossorigin(\s|>)', r'\1\2', content)

        index_html_path.write_text(content, encoding="utf-8")
        logger.info(f"Feature {uid}: patched index.html for Android WebView (removed crossorigin/type=module)")
    except Exception as e:
        logger.warning(f"Feature {uid}: failed to patch index.html: {e}")


def _copy_dist_to_output(ws: Path, folder_path: str, uid: str) -> None:
    """
    Copies the built static files from workspace/dist to folder_path for HTTP serving.
    Also patches index.html for Android WebView compatibility.
    """
    dist_src = ws / "dist"
    dist_dst = Path(folder_path)
    dist_dst.mkdir(parents=True, exist_ok=True)

    # Clean previous output
    for item in dist_dst.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    # Copy new dist contents
    for item in dist_src.iterdir():
        target = dist_dst / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)

    # Patch the output index.html to remove crossorigin (Android WebView fix)
    output_index = dist_dst / "index.html"
    if output_index.exists():
        _patch_index_html_for_webview(output_index, uid)

    logger.info(f"Feature {uid}: copied build dist to {dist_dst}")


# ─── Core Generation Logic ────────────────────────────────────────────────────

def _run_generation(uid: str, gen_id: int, name: str, description: str,
                    folder_path: str) -> None:
    """
    Core generation loop. Handles create & edit in a persistent workspace.
    """
    ws = _setup_workspace(uid)
    existing_code = _get_existing_code(ws)

    system_prompt = build_system_prompt(name, description, existing_code=existing_code)
    current_description = description
    max_clarification_rounds = 3

    for round_num in range(max_clarification_rounds + 1):
        if is_cancelled(gen_id):
            logger.info(f"Gen {gen_id} ({uid}): cancelled at round {round_num}")
            return

        logger.info(f"Gen {gen_id} ({uid}): starting round {round_num} (edit_mode={existing_code is not None})")

        # ── Call Gemini API ────────────────────────────────────────────────────
        try:
            raw_response = _call_gemini(system_prompt, current_description, uid, gen_id)
        except _CancelledError:
            logger.info(f"Gen {gen_id} ({uid}): API call interrupted by cancellation")
            return
        except Exception as e:
            logger.error(f"Gen {gen_id} ({uid}): API call failed: {e}", exc_info=True)
            update_feature_status(uid, STATUS_ERROR, error_msg=f"AI generation failed: {str(e)}")
            return

        # ── Check cancellation after API returns ──────────────────────────────
        if is_cancelled(gen_id):
            logger.info(f"Gen {gen_id} ({uid}): cancelled after API call")
            return

        # ── Parse response ────────────────────────────────────────────────────
        clarification = _try_parse_clarification(raw_response)

        if clarification:
            if round_num >= max_clarification_rounds:
                update_feature_status(
                    uid, STATUS_ERROR,
                    error_msg="AI required too many clarifications. Please provide a more detailed description."
                )
                return

            question = clarification.get("question", "Please provide more details.")
            logger.info(f"Gen {gen_id} ({uid}): clarification needed: {question[:80]}")

            append_chat_message(uid, "assistant", question)
            update_feature_status(uid, STATUS_CLARIFYING)

            # Wait for user answer — get the event for THIS gen_id
            with _lock:
                event = _clarification_events.get(gen_id)
            if event is None:
                return  # Already cleaned up (cancelled)

            logger.info(f"Gen {gen_id} ({uid}): waiting for clarification (up to 30 min)...")
            answered = event.wait(timeout=1800)

            if is_cancelled(gen_id):
                logger.info(f"Gen {gen_id} ({uid}): cancelled while waiting for clarification")
                return

            if not answered:
                update_feature_status(
                    uid, STATUS_ERROR,
                    error_msg="Clarification timed out after 30 minutes. Please try again."
                )
                return

            event.clear()
            with _lock:
                user_answer = _clarification_answers.get(gen_id, "")

            logger.info(f"Gen {gen_id} ({uid}): clarification received: {user_answer[:80]}")

            current_description = (
                f"{description}\n\n"
                f"Additional clarification from user:\n"
                f"Q: {question}\nA: {user_answer}"
            )
            update_feature_status(uid, STATUS_BUILDING)
            continue

        else:
            # ── Got JSX Code response ──────────────────────────────────────────
            if is_cancelled(gen_id):
                logger.info(f"Gen {gen_id} ({uid}): cancelled before writing to disk")
                return

            jsx_content = _extract_clean_jsx(raw_response)

            if not jsx_content or len(jsx_content) < 50:
                logger.error(f"Gen {gen_id} ({uid}): extracted JSX is too short or empty")
                update_feature_status(
                    uid, STATUS_ERROR,
                    error_msg="AI returned an empty or invalid response. Please retry."
                )
                return

            # Write code to workspace src/App.jsx
            app_jsx_file = ws / "src" / "App.jsx"
            app_jsx_file.write_text(jsx_content, encoding="utf-8")
            logger.info(f"Gen {gen_id} ({uid}): wrote {len(jsx_content)} bytes to {app_jsx_file}")

            if is_cancelled(gen_id):
                logger.info(f"Gen {gen_id} ({uid}): cancelled before build")
                return

            # Build production bundle with Vite
            build_success, build_err = _build_workspace(ws, gen_id, uid)
            if not build_success:
                if round_num < max_clarification_rounds:
                    logger.warning(
                        f"Gen {gen_id} ({uid}): build error on round {round_num}, attempting auto-fix: {build_err[:200]}"
                    )
                    current_description = (
                        f"{current_description}\n\n"
                        f"CRITICAL BUILD ERROR: The previous JSX code failed to compile with Vite:\n"
                        f"{build_err}\n\n"
                        f"Please fix the syntax or missing import error and output the complete valid src/App.jsx."
                    )
                    continue
                else:
                    logger.error(f"Gen {gen_id} ({uid}): Vite build failed: {build_err}")
                    update_feature_status(
                        uid, STATUS_ERROR,
                        error_msg=f"App compilation failed: {build_err[:250]}"
                    )
                    return

            if is_cancelled(gen_id):
                logger.info(f"Gen {gen_id} ({uid}): cancelled after build")
                return

            # Copy dist bundle to feature folder_path
            _copy_dist_to_output(ws, folder_path, uid)

            update_feature_status(uid, STATUS_READY)
            append_chat_message(uid, "assistant", "✅ Your feature is ready!")
            logger.info(f"Gen {gen_id} ({uid}): complete. Static files ready at {folder_path}")
            return

    # Exhausted all rounds without completion
    update_feature_status(
        uid, STATUS_ERROR,
        error_msg="Generation loop exhausted. Please try again with a clearer description."
    )


# ─── Cancellation sentinel exception ─────────────────────────────────────────

class _CancelledError(Exception):
    """Raised internally to unwind the call stack on cancellation."""


# ─── Gemini API Call ──────────────────────────────────────────────────────────

def _call_gemini(system_prompt: str, user_message: str, uid: str, gen_id: int) -> str:
    """
    Calls the Gemini Generative Language API.
    """
    if is_cancelled(gen_id):
        raise _CancelledError()

    api_key = os.getenv("ANTIGRAVITY_API_KEY", "")
    if not api_key:
        raise ValueError("ANTIGRAVITY_API_KEY is not set in environment")

    primary_model = os.getenv("ANTIGRAVITY_MODEL", "gemini-3.6-flash")
    fallback_models_str = os.getenv(
        "ANTIGRAVITY_FALLBACK_MODELS",
        "gemini-3.5-flash,gemini-flash-latest,gemini-2.5-flash"
    )
    fallback_models = [m.strip() for m in fallback_models_str.split(",") if m.strip()]

    payload = {
        "system_instruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_message}]
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 65536,
            "responseMimeType": "text/plain",
        }
    }

    models_to_try = [primary_model] + [m for m in fallback_models if m != primary_model]

    last_error: Exception | None = None

    for model_name in models_to_try:
        if is_cancelled(gen_id):
            raise _CancelledError()

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )
        logger.info(f"Gen {gen_id} ({uid}): calling {model_name} ...")

        try:
            resp = http_requests.post(url, json=payload, timeout=180)
        except http_requests.Timeout:
            last_error = RuntimeError(f"Model {model_name} timed out after 180 s")
            logger.warning(f"Gen {gen_id} ({uid}): {last_error}")
            continue
        except Exception as e:
            last_error = e
            logger.warning(f"Gen {gen_id} ({uid}): network error with {model_name}: {e}")
            continue

        if resp.status_code == 429:
            logger.warning(f"Gen {gen_id} ({uid}): {model_name} rate-limited (429), trying fallback")
            last_error = RuntimeError(f"Rate limit hit on {model_name}")
            time.sleep(5)
            continue

        if resp.status_code != 200:
            last_error = RuntimeError(f"API error ({resp.status_code}) from {model_name}: {resp.text[:300]}")
            logger.warning(f"Gen {gen_id} ({uid}): {last_error}")
            continue

        # Successful response
        if is_cancelled(gen_id):
            raise _CancelledError()

        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"Failed to parse JSON response from {model_name}: {e}")

        candidates = data.get("candidates", [])
        if not candidates:
            prompt_feedback = data.get("promptFeedback", {})
            block_reason = prompt_feedback.get("blockReason", "unknown")
            raise RuntimeError(
                f"No candidates returned. Possible safety block: {block_reason}. "
                "Try rephrasing your feature description."
            )

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason", "")

        if finish_reason in ("SAFETY", "RECITATION"):
            raise RuntimeError(
                f"Generation blocked by safety filters ({finish_reason}). "
                "Please rephrase your feature description."
            )

        if finish_reason == "MAX_TOKENS":
            logger.warning(f"Gen {gen_id} ({uid}): MAX_TOKENS hit — response may be truncated")

        parts = candidate.get("content", {}).get("parts", [])
        text_content = "".join(part.get("text", "") for part in parts).strip()

        if not text_content:
            raise RuntimeError(f"Empty text content returned from {model_name}")

        logger.info(f"Gen {gen_id} ({uid}): received {len(text_content)} chars from {model_name}")
        return text_content

    raise RuntimeError(
        f"All models failed. Last error: {last_error}. "
        "Check your API key and network connectivity."
    )


# ─── Response Parsers ─────────────────────────────────────────────────────────

def _try_parse_clarification(response: str) -> dict | None:
    """
    Try to parse the AI response as a clarification JSON object.
    Returns the dict if it's a valid clarification request, else None.
    """
    text = response.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        inner_lines = []
        for line in lines[1:]:
            if line.strip() == "```":
                break
            inner_lines.append(line)
        text = "\n".join(inner_lines).strip()

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        json_candidate = text[brace_start:brace_end + 1]
        try:
            data = json.loads(json_candidate)
            if isinstance(data, dict) and data.get("needs_clarification") is True:
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _extract_clean_jsx(text: str) -> str:
    """
    Extracts clean JSX code for src/App.jsx from the model response.
    Handles ```jsx, ```javascript, ```js, generic ``` fences, or raw code.
    """
    s = text.strip()

    # 1. Match code blocks with language tags
    for tag in ("```jsx", "```tsx", "```javascript", "```js", "```react"):
        if tag in s:
            part = s.split(tag, 1)[1]
            closing = part.find("```")
            if closing != -1:
                return part[:closing].strip()
            return part.strip()

    # 2. Match generic ``` ... ``` fences
    if "```" in s:
        parts = s.split("```")
        for i in range(1, len(parts), 2):
            block = parts[i].strip()
            if "export default" in block or "function App" in block or "import " in block:
                return block

    # 3. If no fences, find the start of code
    lines = s.splitlines()
    code_start_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (stripped.startswith("import ") or
            stripped.startswith("export default") or
            stripped.startswith("function App") or
            stripped.startswith("const App")):
            code_start_idx = i
            break

    if code_start_idx != -1:
        code_lines = lines[code_start_idx:]
        return "\n".join(code_lines).strip()

    return s
