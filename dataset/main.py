from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import json, os, time
from datetime import datetime
from bot import compose, load_json_list, CATEGORIES

app = FastAPI()

# ── In-memory state ───────────────────────────────────────────────────
STATE = {
    "categories": {},
    "merchants": {},
    "customers": {},
    "tick_count": 0,
    "actions_log": []
}

# ── Load expanded data on startup ────────────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))

def get_path(filename):
    for base in ["expanded", "."]:
        p = os.path.join(script_dir, base, filename)
        if os.path.exists(p):
            return p
    return None

def load_state():
    # Merchants
    p = get_path("merchants.json") or get_path("merchants_seed.json")
    if p:
        for m in load_json_list(p):
            STATE["merchants"][m["merchant_id"]] = m

    # Customers
    p = get_path("customers.json") or get_path("customers_seed.json")
    if p:
        for c in load_json_list(p):
            STATE["customers"][c.get("customer_id", c.get("id", ""))] = c

    # Categories
    for cat_dir in ["expanded/categories", "categories"]:
        full = os.path.join(script_dir, cat_dir)
        if os.path.exists(full):
            for fname in os.listdir(full):
                if fname.endswith(".json"):
                    key = fname.replace(".json", "")
                    with open(os.path.join(full, fname)) as f:
                        STATE["categories"][key] = json.load(f)
                        CATEGORIES[key] = STATE["categories"][key]
            break

load_state()
print(f"Loaded {len(STATE['merchants'])} merchants, "
      f"{len(STATE['customers'])} customers, "
      f"{len(STATE['categories'])} categories")

# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metadata")
def metadata():
    return {
        "bot_name": "Vera-Enhanced",
        "version": "1.0.0",
        "author": "Team Tanishq",
        "model": "gemini-2.5-flash",
        "description": "Context-aware merchant growth assistant using 4-context compose framework",
        "capabilities": [
            "research_digest", "perf_spike", "perf_dip",
            "recall_due", "festival_upcoming", "regulation_change",
            "dormant_with_vera", "review_theme_emerged",
            "customer_lapsed_soft", "auto_reply_detection",
            "hinglish_support", "multi_trigger_routing"
        ]
    }


@app.post("/context")
async def context(request: Request):
    body = await request.json()

    # Accept category, merchant, customer updates
    if "categories" in body:
        for cat in body["categories"]:
            slug = cat.get("slug", cat.get("category_slug", ""))
            if slug:
                STATE["categories"][slug] = cat
                CATEGORIES[slug] = cat

    if "merchants" in body:
        for m in body["merchants"]:
            mid = m.get("merchant_id", "")
            if mid:
                STATE["merchants"][mid] = m

    if "customers" in body:
        for c in body["customers"]:
            cid = c.get("customer_id", c.get("id", ""))
            if cid:
                STATE["customers"][cid] = c

    return {
        "status": "ok",
        "loaded": {
            "categories": len(STATE["categories"]),
            "merchants": len(STATE["merchants"]),
            "customers": len(STATE["customers"])
        }
    }


@app.post("/tick")
async def tick(request: Request):
    body = await request.json()
    STATE["tick_count"] += 1

    # Update context if provided
    if "merchants" in body:
        for m in body["merchants"]:
            mid = m.get("merchant_id", "")
            if mid:
                STATE["merchants"][mid] = m

    if "triggers" not in body:
        return {"actions": [], "tick": STATE["tick_count"]}

    triggers = body["triggers"]
    actions = []

    for trigger in triggers[:20]:  # max 20 actions per tick
        try:
            merchant_id = trigger.get("merchant_id")
            customer_id = trigger.get("customer_id")

            merchant = STATE["merchants"].get(merchant_id)
            if not merchant:
                continue

            category_slug = merchant.get("category_slug", "dentists")
            category = STATE["categories"].get(
                category_slug,
                {"slug": category_slug}
            )
            if "slug" not in category:
                category["slug"] = category_slug

            customer = None
            if customer_id:
                customer = STATE["customers"].get(customer_id)

            result = compose(category, merchant, trigger, customer)

            actions.append({
                "trigger_id":       trigger.get("id", ""),
                "merchant_id":      merchant_id,
                "customer_id":      customer_id,
                "message":          result["message"],
                "cta":              result["cta"],
                "send_as_identity": result["send_as_identity"],
                "suppression_key":  result["suppression_key"],
                "rationale":        result["rationale"],
                "timestamp":        datetime.utcnow().isoformat()
            })

            STATE["actions_log"].append(actions[-1])
            time.sleep(1)  # gentle rate limiting

        except Exception as e:
            print(f"Error on trigger {trigger.get('id')}: {e}")
            continue

    return {
        "actions": actions,
        "tick": STATE["tick_count"],
        "total_actions_so_far": len(STATE["actions_log"])
    }


@app.post("/reply")
async def reply(request: Request):
    body = await request.json()

    merchant_id = body.get("merchant_id")
    customer_id = body.get("customer_id")
    message     = body.get("message", "")
    trigger     = body.get("trigger", {
        "id": "reply_trigger",
        "kind": "merchant_reply",
        "scope": "merchant",
        "urgency": 3,
        "suppression_key": f"{merchant_id}:reply:{int(time.time())}"
    })

    merchant = STATE["merchants"].get(merchant_id, {})
    category_slug = merchant.get("category_slug", "dentists")
    category = STATE["categories"].get(category_slug, {"slug": category_slug})
    if "slug" not in category:
        category["slug"] = category_slug

    customer = STATE["customers"].get(customer_id) if customer_id else None

    # Add reply to conversation history
    if merchant:
        hist = merchant.setdefault("conversation_history", [])
        hist.append({
            "role": "merchant",
            "message": message,
            "timestamp": datetime.utcnow().isoformat()
        })

    result = compose(category, merchant, trigger, customer)

    return {
        "message":          result["message"],
        "cta":              result["cta"],
        "send_as_identity": result["send_as_identity"],
        "suppression_key":  result["suppression_key"],
        "rationale":        result["rationale"]
    }