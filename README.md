# Calling Backend

Small FastAPI signaling backend for a one-to-one Flutter audio calling demo.

## Scope

The backend provides:

- User registration, lookup, search, and contacts.
- WebSocket connections at `/ws/{user_id}`.
- One in-memory active call per user.
- Backend-generated call IDs.
- Forwarding for offer, answer, and ICE candidate messages.

Audio is never sent through the backend. The Flutter devices create their own direct WebRTC candidates and exchange them through the WebSocket. No ICE server is configured.

## Run locally

```powershell
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

The default database is local SQLite. Set `DATABASE_URL` for PostgreSQL or another SQLAlchemy-supported database.

## WebSocket protocol

Connect both users to `ws://<host>/ws/<user_id>`.

Caller sends:

```json
{"type":"call_request","to_user_id":"receiver_id"}
```

The backend creates one `call_id`, stores the caller/receiver pair in memory, and sends:

```json
{"type":"incoming_call","call_id":"...","from_user_id":"caller_id"}
{"type":"call_started","call_id":"...","to_user_id":"receiver_id"}
```

The receiver accepts or rejects with:

```json
{"type":"call_accepted","call_id":"..."}
{"type":"call_rejected","call_id":"..."}
```

After acceptance, either participant sends `offer`, `answer`, or `ice_candidate` with the same `call_id` and its payload. The backend verifies membership and forwards the complete message only to the other participant. It does not trust or require a client destination field.

Either participant ends the call with:

```json
{"type":"call_ended","call_id":"..."}
```

The other participant receives the same message. Disconnecting either current WebSocket also ends the active call for both users. A stale socket cannot clear a replacement connection's state.
