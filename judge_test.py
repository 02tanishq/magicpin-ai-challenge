import requests
import time

BASE_URL = "https://web-production-ad428.up.railway.app"
VERSION = int(time.time())

def reset():
    try:
        res = requests.post(f"{BASE_URL}/v1/reset")
        print("Reset:", res.json())
    except:
        print("Reset not available")

def push_context(scope, context_id, payload):
    res = requests.post(f"{BASE_URL}/v1/context", json={
        "scope": scope,
        "context_id": context_id,
        "version": VERSION,
        "payload": payload,
        "delivered_at": "2026-05-03T10:00:00Z"
    })
    print(f"Context [{scope}/{context_id}]:", res.json())

def tick(triggers):
    res = requests.post(f"{BASE_URL}/v1/tick", json={
        "now": "2026-05-03T10:30:00Z",
        "available_triggers": triggers
    })
    data = res.json()
    print("Tick:", data)
    return data

def reply(conv_id, message, merchant_id="m1", turn=2):
    res = requests.post(f"{BASE_URL}/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": merchant_id,
        "from_role": "merchant",
        "message": message,
        "received_at": "2026-05-03T10:35:00Z",
        "turn_number": turn
    })
    print(f"Reply (turn {turn}):", res.json())

def healthz():
    res = requests.get(f"{BASE_URL}/v1/healthz")
    print("Healthz:", res.json())

print("=" * 60)
print("STEP 0 - Health check")
print("=" * 60)
healthz()

print("\n" + "=" * 60)
print("STEP 1 - Reset")
print("=" * 60)
reset()

print("\n" + "=" * 60)
print(f"Using VERSION = {VERSION}")
print("=" * 60)

print("\nSTEP 2 - Push category")
push_context("category", "dentists", {
    "slug": "dentists",
    "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed", "100% safe"]},
    "peer_stats": {"avg_ctr": 0.030, "avg_rating": 4.4},
    "offer_catalog": [{"id": "den_001", "title": "Dental Cleaning @ ₹299", "value": "299"}],
    "digest": [{
        "id": "d_2026W17_jida_fluoride",
        "kind": "research",
        "title": "3-month fluoride recall cuts caries 38% better than 6-month",
        "source": "JIDA Oct 2026, p.14",
        "trial_n": 2100,
        "patient_segment": "high_risk_adults",
        "summary": "Multi-centre Indian trial across 2100 high-risk adult patients"
    }],
    "seasonal_beats": [{"month_range": "Nov-Feb", "note": "exam-stress bruxism spike"}]
})

print("\nSTEP 3 - Push merchant")
push_context("merchant", "m1", {
    "merchant_id": "m1",
    "category_slug": "dentists",
    "identity": {
        "name": "Dr. Meera's Dental Clinic",
        "city": "Delhi",
        "locality": "Lajpat Nagar",
        "verified": True,
        "languages": ["en", "hi"],
        "owner_first_name": "Meera"
    },
    "subscription": {"status": "active", "plan": "Pro", "days_remaining": 82},
    "performance": {
        "window_days": 30,
        "views": 2410,
        "calls": 18,
        "ctr": 0.021,
        "delta_7d": {"views_pct": 0.18, "calls_pct": -0.05}
    },
    "offers": [{"id": "o1", "title": "Dental Cleaning @ ₹299", "value": "299", "status": "active"}],
    "customer_aggregate": {
        "total_unique_ytd": 540,
        "lapsed_180d_plus": 78,
        "high_risk_adult_count": 124,
        "retention_6mo_pct": 0.38
    },
    "signals": ["ctr_below_peer_median", "high_risk_adult_cohort"],
    "conversation_history": [],
    "review_themes": []
})

print("\nSTEP 4 - Push customer")
push_context("customer", "c1", {
    "customer_id": "c1",
    "name": "Priya",
    "state": "lapsed_soft",
    "relationship": {
        "first_visit": "2025-11-04",
        "last_visit": "2025-12-10",
        "visits_total": 3,
        "services_received": ["cleaning", "whitening"]
    },
    "preferences": {
        "preferred_slots": "weekday_evening",
        "channel": "whatsapp",
        "language": "hi"
    },
    "consent": {"opted_in_at": "2025-11-04", "scope": ["recall_reminders"]}
})

print("\nSTEP 5 - Push research trigger")
push_context("trigger", "t1", {
    "id": "t1",
    "scope": "merchant",
    "kind": "research_digest",
    "merchant_id": "m1",
    "customer_id": None,
    "payload": {"category": "dentists", "top_item_id": "d_2026W17_jida_fluoride"},
    "urgency": 2,
    "suppression_key": "research:dentists:2026-W17",
    "expires_at": "2026-05-10T00:00:00Z"
})

print("\nSTEP 6 - Push recall trigger")
push_context("trigger", "t2", {
    "id": "t2",
    "scope": "customer",
    "kind": "recall_due",
    "merchant_id": "m1",
    "customer_id": "c1",
    "payload": {"recall_months": 6},
    "urgency": 3,
    "suppression_key": "recall:c1:6mo",
    "expires_at": "2026-05-10T00:00:00Z"
})

print("\nSTEP 7 - Tick")
result = tick(["t1", "t2"])

print("\nSTEP 8 - Replies")
actions = result.get("actions", [])
if actions:
    conv_id = actions[0]["conversation_id"]
    print("\n--- YES reply ---")
    reply(conv_id, "Yes send it", merchant_id="m1", turn=2)
    time.sleep(2)
    print("\n--- Auto-reply ---")
    reply(conv_id, "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.", merchant_id="m1", turn=3)
    time.sleep(2)
    print("\n--- Auto-reply again ---")
    reply(conv_id, "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.", merchant_id="m1", turn=4)
    time.sleep(2)
    print("\n--- Hostile ---")
    reply(conv_id, "Stop messaging me. This is useless spam.", merchant_id="m1", turn=5)
else:
    print("No actions from tick")

print("\n" + "=" * 60)
print("DONE")
print("=" * 60)
