import asyncio
import json
import os
import sys
import uuid

import websockets

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

WS_URL = "ws://127.0.0.1:8000"


async def receive_json(socket):
    return json.loads(await asyncio.wait_for(socket.recv(), timeout=2.0))


async def run_signaling_tests():
    alice_id = f"alice_{uuid.uuid4().hex[:6]}"
    bob_id = f"bob_{uuid.uuid4().hex[:6]}"
    charlie_id = f"charlie_{uuid.uuid4().hex[:6]}"

    print("--- Offline receiver ---")
    async with websockets.connect(f"{WS_URL}/ws/{alice_id}") as alice:
        await alice.send(json.dumps({"type": "call_request", "to_user_id": "offline"}))
        failed = await receive_json(alice)
        assert failed == {"type": "call_failed", "reason": "offline"}

    print("--- Main call flow ---")
    async with websockets.connect(f"{WS_URL}/ws/{alice_id}") as alice, \
               websockets.connect(f"{WS_URL}/ws/{bob_id}") as bob, \
               websockets.connect(f"{WS_URL}/ws/{charlie_id}") as charlie:
        await alice.send(json.dumps({"type": "call_request", "to_user_id": bob_id}))
        incoming = await receive_json(bob)
        started = await receive_json(alice)
        assert incoming["type"] == "incoming_call"
        assert incoming["call_id"] == started["call_id"]
        call_id = incoming["call_id"]

        await charlie.send(json.dumps({"type": "call_request", "to_user_id": bob_id}))
        assert await receive_json(charlie) == {"type": "call_failed", "reason": "busy"}

        await bob.send(json.dumps({"type": "call_accepted", "call_id": call_id}))
        assert await receive_json(alice) == {"type": "call_accepted", "call_id": call_id}

        offer = {"type": "offer", "call_id": call_id, "sdp": "offer-sdp"}
        await alice.send(json.dumps(offer))
        assert await receive_json(bob) == offer

        answer = {"type": "answer", "call_id": call_id, "sdp": "answer-sdp"}
        await bob.send(json.dumps(answer))
        assert await receive_json(alice) == answer

        candidate = {
            "type": "ice_candidate",
            "call_id": call_id,
            "candidate": {"candidate": "candidate:local"},
        }
        await alice.send(json.dumps(candidate))
        assert await receive_json(bob) == candidate

        await alice.send(json.dumps({"type": "call_ended", "call_id": call_id}))
        assert await receive_json(bob) == {"type": "call_ended", "call_id": call_id}

        # Both users are immediately available for another call on the same sockets.
        await alice.send(json.dumps({"type": "call_request", "to_user_id": bob_id}))
        second_incoming = await receive_json(bob)
        second_started = await receive_json(alice)
        assert second_incoming["call_id"] == second_started["call_id"]
        await bob.send(json.dumps({"type": "call_ended", "call_id": second_incoming["call_id"]}))
        assert await receive_json(alice) == {
            "type": "call_ended",
            "call_id": second_incoming["call_id"],
        }

    print("--- Reject and caller cancel ---")
    async with websockets.connect(f"{WS_URL}/ws/{alice_id}") as alice, \
               websockets.connect(f"{WS_URL}/ws/{bob_id}") as bob:
        await alice.send(json.dumps({"type": "call_request", "to_user_id": bob_id}))
        rejected_call = await receive_json(bob)
        await receive_json(alice)
        await bob.send(json.dumps({"type": "call_rejected", "call_id": rejected_call["call_id"]}))
        assert (await receive_json(alice))["type"] == "call_rejected"

        await alice.send(json.dumps({"type": "call_request", "to_user_id": bob_id}))
        cancelled_call = await receive_json(bob)
        await receive_json(alice)
        await alice.send(json.dumps({"type": "call_ended", "call_id": cancelled_call["call_id"]}))
        assert await receive_json(bob) == {"type": "call_ended", "call_id": cancelled_call["call_id"]}

    print("--- Disconnect cleanup and stale call ID ---")
    async with websockets.connect(f"{WS_URL}/ws/{bob_id}") as bob:
        async with websockets.connect(f"{WS_URL}/ws/{alice_id}") as alice:
            await alice.send(json.dumps({"type": "call_request", "to_user_id": bob_id}))
            disconnect_call = await receive_json(bob)
            await receive_json(alice)
        ended = await receive_json(bob)
        assert ended == {"type": "call_ended", "call_id": disconnect_call["call_id"]}

        async with websockets.connect(f"{WS_URL}/ws/{alice_id}") as alice_again:
            await alice_again.send(json.dumps({"type": "call_request", "to_user_id": bob_id}))
            new_call = await receive_json(bob)
            await receive_json(alice_again)
            assert new_call["call_id"] != disconnect_call["call_id"]
            await alice_again.send(json.dumps({
                "type": "offer",
                "call_id": disconnect_call["call_id"],
                "sdp": "stale",
            }))
            await asyncio.sleep(0.1)
            await alice_again.send(json.dumps({"type": "call_ended", "call_id": new_call["call_id"]}))
            assert await receive_json(bob) == {"type": "call_ended", "call_id": new_call["call_id"]}

    print("ALL SIGNALING TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(run_signaling_tests())
