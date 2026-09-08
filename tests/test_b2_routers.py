import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.database import init_db
from app.main import app


client = TestClient(app)


def make_request(endpoint: str, method: str = "GET", data: dict = None, headers: dict = None):
    req_headers = headers or {}
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"

    if method.upper() == "GET":
        resp = client.get(endpoint, headers=req_headers)
    elif method.upper() == "POST":
        resp = client.post(endpoint, json=data, headers=req_headers)
    elif method.upper() == "DELETE":
        resp = client.delete(endpoint, headers=req_headers)
    else:
        resp = client.request(method, endpoint, json=data, headers=req_headers)

    resp_body = resp.json() if resp.content else None
    return resp.status_code, resp_body



def run_tests():
    print("--- 1. Initializing DB ---")
    init_db()

    # Generate unique phone numbers to avoid previous run collisions
    suffix = str(uuid.uuid4().int % 100000000).zfill(8)
    user1_phone = f"+1555{suffix[:8]}"
    user2_phone = f"+1666{suffix[:8]}"

    print("\n--- 2. Testing POST /users ---")
    # Valid creation
    status, user1 = make_request("/users", "POST", {"name": "Dave Developer", "phone_number": user1_phone})
    assert status == 201, f"Expected 201, got {status}: {user1}"
    assert "id" in user1 and user1["phone_number"] == user1_phone
    user1_id = user1["id"]
    print(f"[PASS] Created User 1: {user1['name']} ({user1_id})")

    status, user2 = make_request("/users", "POST", {"name": "Emma Engineer", "phone_number": user2_phone})
    assert status == 201, f"Expected 201, got {status}: {user2}"
    assert "id" in user2 and user2["phone_number"] == user2_phone
    user2_id = user2["id"]
    print(f"[PASS] Created User 2: {user2['name']} ({user2_id})")

    # Reject duplicate phone number with 409
    status, dup_resp = make_request("/users", "POST", {"name": "Dave Duplicate", "phone_number": user1_phone})
    assert status == 409, f"Expected 409 Conflict, got {status}: {dup_resp}"
    print(f"[PASS] Duplicate phone number correctly rejected with HTTP 409: {dup_resp}")

    status, user_by_phone = make_request(f"/users/by-phone/{user1_phone}")
    assert status == 200 and user_by_phone["id"] == user1_id
    print("[PASS] Phone-number login lookup returned the existing user")

    print("\n--- 3. Testing GET /users/search?q=<term> ---")
    # Partial match on name
    status, results = make_request(f"/users/search?q=dev")
    assert status == 200, f"Expected 200, got {status}: {results}"
    found_dave = any(u["id"] == user1_id for u in results)
    assert found_dave, f"User 1 not found in search results: {results}"
    print("[PASS] Case-insensitive search by name prefix 'dev' succeeded")

    # Partial match on phone number
    status, results = make_request(f"/users/search?q={user2_phone[4:10]}")
    assert status == 200, f"Expected 200, got {status}: {results}"
    found_emma = any(u["id"] == user2_id for u in results)
    assert found_emma, f"User 2 not found in search results: {results}"
    print("[PASS] Partial phone-number search succeeded")

    # Exclude requesting user
    status, results_excluded = make_request(f"/users/search?q=e&user_id={user1_id}")
    assert status == 200
    assert not any(u["id"] == user1_id for u in results_excluded), "Requesting user was not excluded!"
    print("[PASS] Requesting user successfully excluded via user_id query param")

    print("\n--- 4. Testing GET /users/{id} ---")
    # Valid ID lookup
    status, user_fetched = make_request(f"/users/{user1_id}")
    assert status == 200 and user_fetched["id"] == user1_id
    print(f"[PASS] GET /users/{user1_id} returned user details")

    # Invalid ID lookup (404)
    status, not_found = make_request(f"/users/{uuid.uuid4()}")
    assert status == 404, f"Expected 404, got {status}: {not_found}"
    print("[PASS] Non-existent user returned HTTP 404")

    print("\n--- 5. Testing POST /contacts ---")
    # Reject self-add
    status, self_add = make_request("/contacts", "POST", {"user_id": user1_id, "contact_id": user1_id})
    assert status == 400, f"Expected 400 for self-add, got {status}: {self_add}"
    print("[PASS] Self-contact addition rejected with HTTP 400")

    # Validate non-existent user_id
    status, fake_user = make_request("/contacts", "POST", {"user_id": str(uuid.uuid4()), "contact_id": user2_id})
    assert status == 404, f"Expected 404 for non-existent user_id, got {status}: {fake_user}"
    print("[PASS] Non-existent user_id rejected with HTTP 404")

    # Validate non-existent contact_id
    status, fake_contact = make_request("/contacts", "POST", {"user_id": user1_id, "contact_id": str(uuid.uuid4())})
    assert status == 404, f"Expected 404 for non-existent contact_id, got {status}: {fake_contact}"
    print("[PASS] Non-existent contact_id rejected with HTTP 404")

    # Valid contact add
    status, created_contact = make_request("/contacts", "POST", {"user_id": user1_id, "contact_id": user2_id})
    assert status == 201, f"Expected 201, got {status}: {created_contact}"
    assert created_contact["user_id"] == user1_id
    assert created_contact["contact_id"] == user2_id
    assert created_contact["name"] == "Emma Engineer"
    assert created_contact["phone_number"] == user2_phone
    contact_record_id = created_contact["id"]
    print(f"[PASS] Contact created (Contact Table ID={contact_record_id}) joined with name='{created_contact['name']}'")

    # Reject duplicate with 409
    status, dup_contact = make_request("/contacts", "POST", {"user_id": user1_id, "contact_id": user2_id})
    assert status == 409, f"Expected 409 for duplicate contact, got {status}: {dup_contact}"
    print("[PASS] Duplicate contact correctly rejected with HTTP 409")

    print("\n--- 6. Testing GET /contacts/{user_id} ---")
    status, contacts_list = make_request(f"/contacts/{user1_id}")
    assert status == 200, f"Expected 200, got {status}: {contacts_list}"
    assert len(contacts_list) >= 1
    matched = next((c for c in contacts_list if c["id"] == contact_record_id), None)
    assert matched is not None
    assert matched["name"] == "Emma Engineer"
    assert matched["phone_number"] == user2_phone
    print(f"[PASS] GET /contacts/{user1_id} returns contact joined with name/phone: {matched['name']} ({matched['phone_number']})")

    # Non-existent user_id returns 404
    status, contacts_404 = make_request(f"/contacts/{uuid.uuid4()}")
    assert status == 404
    print("[PASS] GET /contacts/{user_id} for non-existent user returns HTTP 404")

    print("\n--- 7. Testing DELETE /contacts/{contact_id} ---")
    # Delete by Contact table's own id
    status, del_resp = make_request(f"/contacts/{contact_record_id}", "DELETE")
    assert status == 204, f"Expected 204 No Content, got {status}: {del_resp}"
    print(f"[PASS] DELETE /contacts/{contact_record_id} returned HTTP 204 No Content")

    # Verify contact is no longer returned
    status, contacts_after = make_request(f"/contacts/{user1_id}")
    assert not any(c["id"] == contact_record_id for c in contacts_after)
    print("[PASS] Verified contact was deleted from database")

    # Deleting again returns 404
    status, del_again = make_request(f"/contacts/{contact_record_id}", "DELETE")
    assert status == 404, f"Expected 404 for already-deleted contact, got {status}: {del_again}"
    print("[PASS] Deleting non-existent contact ID returns HTTP 404")

    print("\n==============================================")
    print("ALL B2 ROUTER REQUIREMENTS VERIFIED AND PASSED!")
    print("==============================================")


if __name__ == "__main__":
    run_tests()
