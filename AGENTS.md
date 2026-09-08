# Project Rules

- The backend generates the only authoritative `call_id`.
- Call-specific client messages contain `call_id` but do not choose a destination; the backend derives the other participant from call state.
- WebRTC media is direct between devices. This backend only relays signaling messages and does not configure or provide ICE servers.
