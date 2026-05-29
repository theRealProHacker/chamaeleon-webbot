import os
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Callable, NotRequired, TypedDict, TypeVar
import queue
import threading

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

_url = os.environ.get("SUPABASE_URL")
_key = os.environ.get("SUPABASE_KEY")
assert _url and _key, (
    "SUPABASE_URL and SUPABASE_KEY must be set in environment variables"
)
supabase: Client = create_client(_url, _key)


class Message(TypedDict):
    role: str  # type: Literal["user", "assistant", "recommendation_previews"]
    content: str | list
    url: NotRequired[str]  # currently not on user messages for some reason
    timestamp: float


type ChatHistory = list[Message]

type SessionID = str


class Session(TypedDict):
    db_id: str
    history: NotRequired[ChatHistory]
    created_at: float
    last_active: float


# sorted old -> new
_sessions: OrderedDict[SessionID, Session] = OrderedDict()
_sessions_by_last_active: OrderedDict[SessionID, None] = OrderedDict()

SESSION_MESSAGE_EXPIRY_SECONDS = 12 * 60 * 60  # 12 hours
SESSION_EXPIRY_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _message_bounds(messages: ChatHistory) -> tuple[float, float]:
    """
    Necessary because some messages might not have timestamps. Somehow our user messages.
    """
    if not messages:
        raise RuntimeError("Cannot derive session bounds from an empty message list")
    first = 0
    for message in messages:
        if "timestamp" in message:
            first = message["timestamp"]
            break
    # last message always has timestamp because it's either bot or recommendation_previews
    return first, messages[-1]["timestamp"]


T = TypeVar("T")


def _execute_with_retries(
    action: Callable[[], T], failure_message: str, session_id: SessionID
) -> T:
    max_retries = 3
    last_error: Exception | None = None
    last_response: object | None = None

    for attempt in range(max_retries):
        try:
            return action()
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            continue

    raise last_error or RuntimeError(
        failure_message.format(session_id=session_id)
        + f" after {max_retries} attempts. Last response: {last_response}"
    )


def _db_query_all(cutoff: str):
    return (
        supabase.table("chats")
        .select("id, session_id, messages, timestamp")
        .gte("timestamp", cutoff)
        .order("timestamp", desc=False)
        .execute()
    )


def _load_sessions_from_db() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    response = _execute_with_retries(
        lambda: _db_query_all(cutoff),
        "Failed to load chat sessions for session {session_id}",
        "startup",
    )

    rows = response.data
    if not isinstance(rows, list):
        raise RuntimeError(
            f"Failed to load chat sessions for session startup: {response}"
        )

    now = time.time()
    loaded_sessions: list[tuple[SessionID, Session]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        session_id = row.get("session_id")
        db_id = row.get("id")
        messages: ChatHistory = row.get("messages")  # type: ignore

        if not isinstance(session_id, str):
            continue

        if not isinstance(db_id, str) or not isinstance(messages, list) or not messages:
            print(f"Skipping invalid session row with id {session_id}: {row}")
            continue

        created_at, last_active = _message_bounds(messages)

        session: Session = {
            "db_id": db_id,
            "created_at": created_at,
            "last_active": last_active,
        }
        if now - last_active <= SESSION_MESSAGE_EXPIRY_SECONDS:
            session["history"] = list(messages)

        loaded_sessions.append((session_id, session))

    global _sessions, _sessions_by_last_active
    _sessions = OrderedDict(loaded_sessions)
    _sessions_by_last_active = OrderedDict(
        sorted(
            ((sid, None) for sid in _sessions),
            key=lambda item: _sessions[item[0]]["last_active"],
        )
    )


def _prune_sessions() -> None:
    now = time.time()
    session_cutoff = now - SESSION_EXPIRY_SECONDS
    message_cutoff = now - SESSION_MESSAGE_EXPIRY_SECONDS

    expired_session_ids = []
    for sid, session in _sessions.items():
        if session["created_at"] >= session_cutoff:
            break
        expired_session_ids.append(sid)

    for sid in expired_session_ids:
        del _sessions[sid]
        _sessions_by_last_active.pop(sid, None)

    for session_id in _sessions_by_last_active:
        session = _sessions[session_id]
        if session["last_active"] >= message_cutoff:
            break
        session.pop("history", None)


def _fetch_chat_history(db_id: str, session_id: SessionID) -> ChatHistory:
    """Fetch chat history from Supabase with retries."""
    response = _execute_with_retries(
        lambda: supabase.table("chats")
        .select("session_id, messages")
        .eq("id", db_id)
        .execute(),
        "Failed to fetch chat history for session {session_id}",
        session_id,
    )

    data = response.data
    if not isinstance(data, list) or not data:
        raise RuntimeError(
            f"Failed to fetch chat history for session {session_id}: {response}"
        )

    first_row = data[0]
    if not isinstance(first_row, dict) or "messages" not in first_row:
        raise RuntimeError(
            f"Failed to fetch chat history for session {session_id}: {response}"
        )

    history: ChatHistory = first_row["messages"]  # type: ignore
    if not isinstance(history, list):
        raise RuntimeError(
            f"Failed to fetch chat history for session {session_id}: {response}"
        )

    if first_row["session_id"] != session_id:
        raise RuntimeError(
            f"Fetched session_id {first_row['session_id']} does not match expected {session_id}"
        )

    return history


def log_messages(session_id: SessionID, messages: ChatHistory) -> None:
    if not session_id:
        raise ValueError("session_id must not be empty")

    _prune_sessions()

    if session_id not in _sessions:
        # TODO: check if the session_id already exists in DB to avoid duplicates
        # --- New session: INSERT ---
        row = {
            "session_id": session_id,
            "messages": messages,
        }
        response = supabase.table("chats").insert(row).execute()
        # if response["status"] != 201:
        #     raise RuntimeError(f"Failed to insert chat log: {response}")

        data = response.data

        first_row = data[0]
        if not isinstance(first_row, dict) or "id" not in first_row:
            raise RuntimeError(f"Failed to insert chat log: {response}")

        db_id: str = first_row["id"]  # type: ignore

        if DEBUG:
            print(f"Inserted chat log: {db_id} with messages: {messages}")

        now = time.time()

        session: Session = {
            "db_id": db_id,
            "history": messages,
            "created_at": now,
            "last_active": now,
        }

        _sessions[session_id] = session
        _sessions_by_last_active[session_id] = None

    else:
        # --- Existing session: UPDATE ---
        session = _sessions[session_id]
        session["last_active"] = time.time()
        db_id = session["db_id"]
        # update last active sorting
        _sessions_by_last_active.pop(session_id, None)
        _sessions_by_last_active[session_id] = None

        # Merge
        if "history" not in session:
            history = _fetch_chat_history(db_id, session_id)
        else:
            history = session["history"]
        history += messages

        # TODO: optimize by only sending new messages instead of whole history on every update
        update_payload = {
            "messages": history,
        }
        supabase.table("chats").update(update_payload).eq("id", db_id).execute()  # type: ignore
        session["history"] = history


def active_session_count() -> int:
    _prune_sessions()
    return len(_sessions)


############## Running #############

################## Load sessions on import ####################################
_load_sessions_from_db()

print("Active Sessions:", active_session_count())

################## Log worker + queue ####################################
log_queue: queue.Queue = queue.Queue()


def _log_worker():
    """Single background thread — processes logging tasks one at a time."""
    while True:
        task = log_queue.get()
        if task is None:  # poison pill to shut down
            break
        try:
            task()
        except Exception as e:
            print(f"[log_worker] Error: {e}")
        finally:
            log_queue.task_done()


# Start exactly ONE worker thread at module load time
threading.Thread(target=_log_worker, daemon=True, name="log-worker").start()
