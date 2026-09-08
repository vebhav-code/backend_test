import json
import logging
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.connection_manager import Call, manager


logger = logging.getLogger("signaling.ws")
router = APIRouter(tags=["Signaling WebSocket"])


def _call_error(reason: str) -> Dict[str, str]:
    return {"type": "call_failed", "reason": reason}


async def _send_call_ended(call: Call, sender_id: str):
    partner_id = call.receiver_id if call.caller_id == sender_id else call.caller_id
    logger.info(
        "[Signaling] %s -> %s | type='call_ended' | call_id='%s'",
        sender_id,
        partner_id,
        call.call_id,
    )
    await manager.send_to(partner_id, {"type": "call_ended", "call_id": call.call_id})


@router.websocket("/ws/{user_id}")
async def websocket_signaling_endpoint(websocket: WebSocket, user_id: str):
    user_id = user_id.strip()
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="user_id is required")
        return

    await manager.connect(user_id, websocket)

    try:
        while True:
            try:
                message = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                logger.warning("[Signaling] Dropped malformed JSON from user '%s'", user_id)
                continue

            if not isinstance(message, dict):
                continue

            message_type = message.get("type")
            if message_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if not message_type:
                continue

            if message_type == "call_request":
                receiver_id = str(message.get("to_user_id", "")).strip()
                logger.info("[Signaling] %s -> %s | type='call_request'", user_id, receiver_id)
                if not receiver_id:
                    continue

                call, reason = await manager.create_call(user_id, receiver_id)
                if call is None:
                    logger.info(
                        "[Signaling] system -> %s | type='call_failed' | reason='%s'",
                        user_id,
                        reason,
                    )
                    await manager.send_to(user_id, _call_error(reason or "unavailable"))
                    continue

                incoming = {
                    "type": "incoming_call",
                    "call_id": call.call_id,
                    "from_user_id": user_id,
                }
                logger.info(
                    "[Signaling] %s -> %s | type='incoming_call' | call_id='%s'",
                    user_id,
                    receiver_id,
                    call.call_id,
                )
                if not await manager.send_to(receiver_id, incoming):
                    manager.end_call(call.call_id)
                    await manager.send_to(user_id, _call_error("offline"))
                    continue

                await manager.send_to(
                    user_id,
                    {"type": "call_started", "call_id": call.call_id, "to_user_id": receiver_id},
                )
                continue

            call_id = message.get("call_id")
            call = manager.get_call(call_id) if isinstance(call_id, str) else None

            if message_type == "call_accepted":
                if call is None or call.state != "ringing" or call.receiver_id != user_id:
                    await manager.send_to(user_id, _call_error("invalid_call"))
                    continue
                manager.set_state(call.call_id, "accepted")
                logger.info(
                    "[Signaling] %s -> %s | type='call_accepted' | call_id='%s'",
                    user_id,
                    call.caller_id,
                    call.call_id,
                )
                await manager.send_to(call.caller_id, {"type": "call_accepted", "call_id": call.call_id})
                continue

            if message_type == "call_rejected":
                if call is None or call.state != "ringing" or call.receiver_id != user_id:
                    await manager.send_to(user_id, _call_error("invalid_call"))
                    continue
                logger.info(
                    "[Signaling] %s -> %s | type='call_rejected' | call_id='%s'",
                    user_id,
                    call.caller_id,
                    call.call_id,
                )
                await manager.send_to(call.caller_id, {"type": "call_rejected", "call_id": call.call_id})
                manager.end_call(call.call_id)
                continue

            if message_type == "call_ended":
                if call is None or not manager.is_participant(call.call_id, user_id):
                    await manager.send_to(user_id, _call_error("invalid_call"))
                    continue
                await _send_call_ended(call, user_id)
                manager.end_call(call.call_id)
                continue

            if message_type in {"offer", "answer", "ice_candidate"}:
                if call is None or not manager.is_participant(call.call_id, user_id):
                    logger.warning(
                        "[Signaling] Ignored %s from '%s' for unauthorized call '%s'",
                        message_type,
                        user_id,
                        call_id,
                    )
                    continue
                if call.state not in {"accepted", "connected"}:
                    await manager.send_to(user_id, _call_error("call_not_accepted"))
                    continue

                partner_id = manager.get_call_partner(user_id)
                if partner_id is None:
                    continue
                manager.set_state(call.call_id, "connected")
                logger.info(
                    "[Signaling] %s -> %s | type='%s' | call_id='%s'",
                    user_id,
                    partner_id,
                    message_type,
                    call.call_id,
                )
                await manager.send_to(partner_id, message)
                continue

            logger.info("[Signaling] Unrecognized message type '%s' from '%s'", message_type, user_id)

    except WebSocketDisconnect:
        logger.info("[Signaling] WebSocket disconnected for user '%s'", user_id)
    except Exception as exc:
        logger.error("[Signaling] WebSocket error for user '%s': %s", user_id, exc)
    finally:
        if manager.is_current_connection(user_id, websocket):
            call = manager.get_user_call(user_id)
            if call is not None:
                await _send_call_ended(call, user_id)
                manager.end_call(call.call_id)
            manager.disconnect(user_id, websocket)
