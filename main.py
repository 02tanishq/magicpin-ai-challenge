import os
import json
import time
import uuid
from datetime import datetime
from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Any, Optional
from dataset.bot import compose, CATEGORIES
import re

app = FastAPI()
START = time.time()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ── In-memory stores ──────────────────────────────────────────────────
contexts: dict[tuple, dict] = {}       # (scope, context_id) -> {version, payload}
conversations: dict[str, list] = {}   # conversation_id -> [turns]

# ── Helpers ───────────────────────────────────────────────────────────
def get_payload(scope, context_id):
    return contexts.get((scope, context_id), {}).get("payload")

def all_payloads(scope):
    return {cid: v["payload"] for (s, cid), v in contexts.items() if s == scope}

# ── Load base dataset on startup ──────────────────────────────────────
def load_base_dataset():
    script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")
    for base in ["expanded", "."]:
        cat_dir = os.path.join(script_dir, base, "categories")
        if os.path.exists(cat_dir):
            for fname in os.listdir(cat_dir):
                if fname.endswith(".json"):
                    slug = fname.replace(".json", "")
                    with open(os.path.join(cat_dir, fname)) as f:
                        payload = json.load(f)
                    contexts[("category", slug)] = {"version": 0, "payload": payload}
                    CATEGORIES[slug] = payload
            break

    def load_list(path):
        with open(path) as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return raw
        for v in raw.values():
            if isinstance(v, list):
                return v
        return [raw]

    script_dir2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")
    for base in ["expanded", "."]:
        mp = os.path.join(script_dir2, base, "merchants.json")
        if not os.path.exists(mp):
            mp = os.path.join(script_dir2, "merchants_seed.json")
        if os.path.exists(mp):
            for m in load_list(mp):
                mid = m.get("merchant_id", "")
                if mid:
                    contexts[("merchant", mid)] = {"version": 0, "payload": m}
            break

    for base in ["expanded", "."]:
        cp = os.path.join(script_dir2, base, "customers.json")
        if not os.path.exists(cp):
            cp = os.path.join(script_dir2, "customers_seed.json")
        if os.path.exists(cp):
            for c in load_list(cp):
                cid = c.get("customer_id", c.get("id", ""))
                if cid:
                    contexts[("customer", cid)] = {"version": 0, "payload": c}
            break

    for base in ["expanded", "."]:
        tp = os.path.join(script_dir2, base, "triggers.json")
        if not os.path.exists(tp):
            tp = os.path.join(script_dir2, "triggers_seed.json")
        if os.path.exists(tp):
            for t in load_list(tp):
                tid = t.get("id", "")
                if tid:
                    contexts[("trigger", tid)] = {"version": 0, "payload": t}
            break

load_base_dataset()
counts = {s: sum(1 for (sc,_) in contexts if sc==s) for s in ["category","merchant","customer","trigger"]}
print(f"Loaded: {counts}")

# ── 1. GET /v1/healthz ────────────────────────────────────────────────
@app.get("/v1/healthz")
@app.get("/healthz")
async def healthz():
    counts = {s: sum(1 for (sc,_) in contexts if sc==s)
              for s in ["category","merchant","customer","trigger"]}
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START),
        "contexts_loaded": counts
    }

@app.get("/v1/healthz")

@app.get("/healthz")

async def healthz():

    counts = {s: sum(1 for (sc,_) in contexts if sc==s)

              for s in ["category","merchant","customer","trigger"]}

    return {

        "status": "ok",

        "uptime_seconds": int(time.time() - START),

        "contexts_loaded": counts

    }

# debug_endpoint
@app.get("/v1/debug")
async def debug():
    key = os.environ.get("GEMINI_API_KEY", "")
    return {
        "key_set": bool(key),
        "key_length": len(key),
        "key_prefix": key[:8] if key else "MISSING"
    }

# ── 2. GET /v1/metadata ───────────────────────────────────────────────
@app.get("/v1/metadata")
@app.get("/metadata")
async def metadata():
    return {
        "team_name":    "Team Tanishq",
        "team_members": ["Tanishq"],
        "model":        "gemini-2.5-flash",
        "approach":     "4-context composer with trigger routing, Hinglish support, anti-repetition, auto-reply detection",
        "contact_email":"tanishq@example.com",
        "version":      "2.0.0",
        "submitted_at": "2026-05-03T00:00:00Z"
    }

# ── 3. POST /v1/context ───────────────────────────────────────────────
class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str = ""

@app.post("/v1/context")
@app.post("/context")
async def push_context(request: Request):
    try:
        raw = await request.json()
    except:
        return {"accepted": False, "reason": "invalid_json"}

    scope      = raw.get("scope", "")
    context_id = raw.get("context_id") or raw.get("id","")
    version = int(raw.get("version", int(time.time())))
    payload = raw.get("payload", raw)

# If judge sends full category JSON directly as body
    if not scope and "slug" in raw:
        scope      = "category"
        context_id = context_id or raw.get("slug", "")
        payload    = raw

    # Fallback: detect scope from payload shape
    if not scope:
        if "merchant_id" in payload:
            scope = "merchant"
            context_id = context_id or payload.get("merchant_id","")
        elif "customer_id" in payload:
            scope = "customer"
            context_id = context_id or payload.get("customer_id","")
        elif "slug" in payload:
            scope = "category"
            context_id = context_id or payload.get("slug","")
        elif "kind" in payload:
            scope = "trigger"
            context_id = context_id or payload.get("id","")

    if not scope or not context_id:
        return {"accepted": False, "reason": "missing_scope_or_id"}

    key = (scope, context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= version:
        return {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}

    contexts[key] = {"version": int(version), "payload": payload}

    # Sync categories to CATEGORIES global
    if scope == "category":
        CATEGORIES[context_id] = payload
        slug = payload.get("slug", context_id)
        CATEGORIES[slug] = payload

    return {
        "accepted":  True,
        "ack_id":    f"ack_{context_id}_v{version}",
        "stored_at": datetime.utcnow().isoformat() + "Z"
    }

# ── 4. POST /v1/tick ──────────────────────────────────────────────────
class TickBody(BaseModel):
    now: str = ""
    available_triggers: list[str] = []

@app.post("/v1/tick")
@app.post("/tick")
async def tick(body: TickBody):
    actions = []
    used_merchants = set()

    for trg_id in body.available_triggers[:20]:
        trg = get_payload("trigger", trg_id)
        if not trg:
            continue

        merchant_id = trg.get("merchant_id")
        trigger_kind = trg.get("kind", "")
        if not merchant_id:
            continue
        trigger_key = f"{merchant_id}:{trigger_kind}"
        if trigger_key in used_merchants:
            continue

        merchant = get_payload("merchant", merchant_id)
        if not merchant:
            continue

        category_slug = merchant.get("category_slug", "")
        category = get_payload("category", category_slug) or {"slug": category_slug}
        if "slug" not in category:
            category["slug"] = category_slug

        customer_id = trg.get("customer_id")
        customer = get_payload("customer", customer_id) if customer_id else None

        try:
            result = compose(category, merchant, trg, customer)
            if not result.get("message"):
                continue

            conv_id = f"conv_{merchant_id}_{trg_id}_{int(time.time())}"
            conversations[conv_id] = [{
                "from": "vera",
                "msg":  result["message"],
                "at":   datetime.utcnow().isoformat()
            }]

            actions.append({
                "conversation_id": conv_id,
                "merchant_id":     merchant_id,
                "customer_id":     customer_id,
                "send_as":         result.get("send_as_identity", "vera"),
                "trigger_id":      trg_id,
                "template_name":   f"vera_{trg.get('kind','generic')}_v2",
                "template_params": [
                    merchant.get("identity", {}).get("name", "Merchant"),
                    trg.get("kind", ""),
                    result["message"][:80]
                ],
                "body":            result["message"],
                "cta":             result.get("cta", "open_ended"),
                "suppression_key": result.get("suppression_key", trg.get("suppression_key", "")),
                "rationale":       result.get("rationale", "")
            })

            used_merchants.add(f"{merchant_id}:{trigger_kind}")
            time.sleep(0.5)

        except Exception as e:
            print(f"Tick error for {trg_id}: {e}")
            continue

    return {"actions": actions}

# ── 5. POST /v1/reply ─────────────────────────────────────────────────
class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str = ""
    received_at: str = ""
    turn_number: int = 1

@app.post("/v1/reply")
@app.post("/reply")
async def reply(body: ReplyBody):
    msg = body.message.lower().strip()

    # Log the turn
    conversations.setdefault(body.conversation_id, []).append({
        "from": body.from_role,
        "msg":  body.message,
        "at":   datetime.utcnow().isoformat()
    })

    # Hostile detection → end conversation
    hostile = ["stop", "spam", "useless", "don't message", "mat bhejo",
               "band karo", "not interested", "remove me", "unsubscribe"]
    if any(p in msg for p in hostile):
        return {
            "action":    "end",
            "rationale": "Merchant signaled not interested — gracefully exiting"
        }

    # Intent to proceed → send action message
    intent_yes = ["ok lets do it", "yes", "haan", "go ahead", "start",
                  "karo", "let's do", "whats next", "next", "sure", "sounds good",
                  "send it", "bhejo", "theek hai"]
    if any(p in msg for p in intent_yes):
        return {
            "action":    "send",
            "body":      "Setting this up for you right now — I'll send a confirmation once it's live. Should take less than 2 minutes. ✅",
            "cta":       "open_ended",
            "rationale": "Merchant confirmed intent — routing to action mode immediately"
        }

    # Auto-reply detection → wait
    auto_reply_phrases = ["thank you for contacting", "thanks for contacting",
                          "we will get back", "we'll get back", "our team will",
                          "please leave a message", "currently unavailable"]
    if any(p in msg for p in auto_reply_phrases):
        # Check if 3+ auto-replies in a row
        hist = conversations.get(body.conversation_id, [])
        recent = [t for t in hist[-6:] if t.get("from") == "merchant"]
        if len(recent) >= 3:
            return {
                "action":       "wait",
                "wait_seconds": 3600,
                "rationale":    "3+ consecutive auto-replies detected — backing off 1 hour"
            }
        if len(recent) >= 2:
            return {
                "action":       "wait",
                "wait_seconds": 86400,
                "rationale":    "Same auto-reply twice — owner not at phone. Wait 24h."
            }
        return {
            "action":    "send",
            "body":      "Looks like an auto-reply 😊 When you're back, just reply 'Yes' to continue.",
            "cta":       "binary_yes_no",
            "rationale": "First auto-reply detected — prompting real user"
        }

    # Normal reply → compose response
    merchant_id = body.merchant_id
    merchant = get_payload("merchant", merchant_id) if merchant_id else {}

    if not merchant:
        merchant = {}

    merchant.setdefault("identity", {"name": "Merchant"})
    merchant.setdefault("category_slug", "restaurants")

    if not merchant:
        merchant = {
            "merchant_id":          merchant_id or "unknown",
            "category_slug":        "restaurants",
            "identity":             {"name": "Merchant", "locality": "your area"},
            "performance":          {"views": 0, "calls": 0},
            "offers":               [],
            "conversation_history": [],
            "customer_aggregate":   {},
            "signals":              [],
            "review_themes":        [],
            "subscription":         {}
        }

    category_slug = merchant.get("category_slug", "restaurants")
    category = get_payload("category", category_slug) or {"slug": category_slug}
    if "slug" not in category:
        category["slug"] = category_slug

    customer = get_payload("customer", body.customer_id) if body.customer_id else None

    trigger = {
        "id":             f"reply_{body.conversation_id}",
        "kind":           "merchant_reply",
        "scope":          "merchant",
        "urgency":        3,
        "suppression_key": f"{merchant_id}:reply:{body.turn_number}",
        "payload":        {}
    }

    # Add conversation history to merchant
    merchant["conversation_history"] = conversations.get(body.conversation_id, [])

    try:
        result = compose(category, merchant, trigger, customer)
        response_body = result.get("message", "")
    except Exception as e:
        print(f"Reply compose error: {e}")
        response_body = "Thanks for your reply! I'll follow up with something useful shortly."

    if not response_body:
        response_body = "Got it! Let me put something together for you."

    conversations[body.conversation_id].append({
        "from": "vera",
        "msg":  response_body,
        "at":   datetime.utcnow().isoformat()
    })
    
    response_body =re.sub(r'https?://\S+', '',response_body)
    return {
        "action":    "send",
        "body":      response_body.strip(),
        "cta":       result.get("cta", "open_ended") if "result" in dir() else "open_ended",
        "rationale": result.get("rationale", "") if "result" in dir() else ""
    }

# ── Optional teardown ─────────────────────────────────────────────────
@app.post("/v1/teardown")
async def teardown():
    contexts.clear()
    conversations.clear()
    return {"status": "wiped"}

@app.post("/v1/reset")
async def reset():
    contexts.clear()
    conversations.clear()
    return {"status": "cleared"}

@app.get("/v1/test-llm")
async def test_llm():
    import requests as req
    key = os.environ.get("GEMINI_API_KEY", "")
    try:
        r = req.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": "Say hello in 5 words"}]}]},
            timeout=20
        )
        return {"status": r.status_code, "response": r.json()}
    except Exception as e:
        return {"error": str(e)}
