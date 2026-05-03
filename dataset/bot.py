"""
Vera — magicpin AI Challenge
bot.py: The compose() engine + all helpers
Optimized against the 10 case studies in examples/case-studies.md
"""

import json
import os
import time
import re
import requests
from datetime import datetime

# ── API setup ─────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Debug on startup
print(f"[bot.py] GEMINI_API_KEY loaded: length={len(GEMINI_API_KEY)}, prefix={GEMINI_API_KEY[:8] if GEMINI_API_KEY else 'EMPTY'}")

# ── Category voice rules ──────────────────────────────────────────────
# Derived from case studies — what the judge scores on
CATEGORY_VOICE = {
    "dentists": {
        "tone": "peer_clinical — write like a smart fellow dentist, not a salesperson",
        "vocab": ["fluoride varnish", "caries", "recall window", "high-risk cohort", "scaling", "OPG"],
        "taboos": ["guaranteed", "100% safe", "completely cure", "miracle", "best in city", "amazing"],
        "emoji": "🦷",
        "register": "Dr. {owner} — use full title for dentists"
    },
    "restaurants": {
        "tone": "visual and FOMO-driven — make them hungry, create urgency",
        "vocab": ["covers", "AOV", "footfall", "table turns", "delivery", "dine-in"],
        "taboos": ["yummy", "delicious food", "best taste", "cheapest"],
        "emoji": "🍽️",
        "register": "{owner} — first name only"
    },
    "salons": {
        "tone": "warm-practical — fellow operator, aspirational but grounded",
        "vocab": ["retail", "rebooking", "client retention", "chair utilization", "keratin", "bridal"],
        "taboos": ["cheapest", "discount", "amazing results"],
        "emoji": "💇",
        "register": "{owner} — first name only"
    },
    "gyms": {
        "tone": "coach-to-operator — data-driven, no hype, peer benchmark aware",
        "vocab": ["active members", "churn", "ad spend", "conversion", "HIIT", "attendance"],
        "taboos": ["guaranteed results", "best gym", "amazing transformation"],
        "emoji": "💪",
        "register": "{owner} — first name only"
    },
    "pharmacies": {
        "tone": "trustworthy-precise — clinical accuracy, respectful, no alarm",
        "vocab": ["chronic-Rx", "dispensed", "batch", "sub-potency", "compliance", "refill"],
        "taboos": ["cheapest medicines", "miracle cure", "guaranteed health"],
        "emoji": "💊",
        "register": "{owner} — first name only"
    }
}

# ── Load categories ───────────────────────────────────────────────────
def load_categories():
    cats = {}
    for cat_dir in [
        "expanded/categories",
        "categories",
        os.path.join(os.path.dirname(__file__), "expanded/categories"),
        os.path.join(os.path.dirname(__file__), "categories"),
    ]:
        if os.path.exists(cat_dir):
            for fname in os.listdir(cat_dir):
                if fname.endswith(".json"):
                    key = fname.replace(".json", "")
                    with open(os.path.join(cat_dir, fname)) as f:
                        cats[key] = json.load(f)
            break
    return cats

CATEGORIES = load_categories()

# ── Load json list helper ─────────────────────────────────────────────
def load_json_list(filepath):
    with open(filepath) as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for v in raw.values():
            if isinstance(v, list):
                return v
    return [raw]

# ── Auto-reply detection ──────────────────────────────────────────────
AUTO_REPLY_PHRASES = [
    "thank you for contacting", "thanks for contacting",
    "we will get back", "we'll get back", "our team will",
    "please leave a message", "currently unavailable", "out of office",
    "dhanyavaad", "shukriya", "automated response", "auto reply"
]

def is_auto_reply(message: str) -> bool:
    if not message:
        return False
    return any(p in message.lower() for p in AUTO_REPLY_PHRASES)

# ── Language detection ────────────────────────────────────────────────
def get_language_instruction(merchant, customer=None):
    # Check customer preference first
    if customer:
        lang = customer.get("preferences", {}).get("language", "")
        if lang == "hi":
            return "Use natural Hindi-English mix (Hinglish). E.g. 'Apke liye 2 slots ready hain'"
    # Check merchant identity
    identity = merchant.get("identity", {})
    languages = identity.get("languages", ["en"])
    if "hi" in languages:
        return "Use natural Hindi-English mix (Hinglish) — blend smoothly, not forced"
    return "Write in clear English"

# ── Get best offer ────────────────────────────────────────────────────
def pick_best_offer(merchant, trigger_kind, category_slug):
    offers = merchant.get("offers", [])

    if offers:
        # For dip/recall prefer discounted offers
        if trigger_kind in ("perf_dip", "performance_dip", "recall_due",
                            "customer_lapsed_soft", "customer_lapsed_hard"):
            discounted = [o for o in offers if
                          o.get("discounted_price") or o.get("discount_pct") or
                          o.get("value") or "@" in str(o.get("title", ""))]
            if discounted:
                return discounted[0]
        return offers[0]

    # Category catalog fallback
    cat = CATEGORIES.get(category_slug, {})
    catalog = cat.get("offer_catalog", cat.get("offers", []))
    if catalog:
        return catalog[0]

    # Hardcoded last resort
    fallbacks = {
        "dentists":    {"title": "Dental Cleaning @ ₹299",      "value": "299"},
        "restaurants": {"title": "Special Thali @ ₹199",        "value": "199"},
        "salons":      {"title": "Haircut & Styling @ ₹349",    "value": "349"},
        "gyms":        {"title": "Monthly Membership @ ₹999",   "value": "999"},
        "pharmacies":  {"title": "Health Checkup Package @ ₹499", "value": "499"},
    }
    return fallbacks.get(category_slug, {"title": "Special Offer @ ₹299", "value": "299"})

def offer_name(offer):
    """Extract clean name from offer."""
    title = offer.get("name", offer.get("title", offer.get("service_name", "")))
    if "@" in title:
        return title.split("@")[0].strip()
    return title or "Special Offer"

def offer_price(offer):
    """Extract price from offer."""
    price = offer.get("discounted_price", offer.get("price",
            offer.get("value", "")))
    if not price and "@" in str(offer.get("title", "")):
        raw = offer.get("title", "").split("@")[-1].strip()
        price = raw.replace("₹", "").strip().split()[0]
    return str(price) if price else ""

# ── Get digest item ───────────────────────────────────────────────────
def get_digest_item(category_slug, top_item_id=None):
    """Get best matching digest item from category."""
    cat = CATEGORIES.get(category_slug, {})
    digest = cat.get("digest", [])
    if not digest:
        return None
    if top_item_id:
        for d in digest:
            if d.get("id") == top_item_id:
                return d
    return digest[0]

# ── Get peer stats ────────────────────────────────────────────────────
def get_peer_stats(category_slug):
    cat = CATEGORIES.get(category_slug, {})
    return cat.get("peer_stats", {"avg_ctr": 0.030, "avg_rating": 4.4})

# ── Clean item ID ─────────────────────────────────────────────────────
def clean_item_id(item_id: str) -> str:
    parts = item_id.split("_")
    if len(parts) > 2:
        return " ".join(parts[2:]).replace("-", " ").title()
    return item_id.replace("_", " ").replace("-", " ").title()

# ── Build the rich context block for LLM ─────────────────────────────
def build_context_block(category_slug, merchant, trigger, customer=None):
    identity  = merchant.get("identity", {})
    perf      = merchant.get("performance", {})
    delta     = perf.get("delta_7d", {})
    subs      = merchant.get("subscription", {})
    signals   = merchant.get("signals", [])
    reviews   = merchant.get("review_themes", [])
    cust_agg  = merchant.get("customer_aggregate", {})
    conv_hist = merchant.get("conversation_history", [])
    payload   = trigger.get("payload", {})

    # Peer comparison
    peer      = get_peer_stats(category_slug)
    m_ctr     = perf.get("ctr", 0)
    p_ctr     = peer.get("avg_ctr", 0.030)
    ctr_diff  = round((m_ctr - p_ctr) * 100, 1)
    ctr_note  = (f"CTR {m_ctr:.1%} — {abs(ctr_diff):.1f}pp "
                 f"{'above' if ctr_diff >= 0 else 'BELOW'} peer median {p_ctr:.1%}")

    # Digest
    top_item_id = payload.get("top_item_id", "")
    digest = get_digest_item(category_slug, top_item_id)
    digest_block = ""
    if digest:
        digest_block = f"""
DIGEST ITEM (use this as the hook — cite source + numbers):
- Title: {digest.get('title', digest.get('headline', ''))}
- Source: {digest.get('source', 'JIDA')}
- Trial N: {digest.get('trial_n', '')}
- Patient segment: {digest.get('patient_segment', '')}
- Summary: {digest.get('summary', '')}
- Key stat: {digest.get('key_stat', '')}"""

    # Signals
    signals_str = ", ".join(
        s.get("kind", s) if isinstance(s, dict) else str(s)
        for s in signals
    ) or "none"

    # Anti-repetition
    last_msgs = [t.get("msg", t.get("message", "")) for t in conv_hist[-3:]]
    last_str  = " | ".join(last_msgs) if last_msgs else "none"

    # Customer block
    cust_block = ""
    if customer:
        rel   = customer.get("relationship", {})
        prefs = customer.get("preferences", {})
        state = customer.get("state", "unknown")
        cname = customer.get("name", customer.get("first_name", "Customer"))
        cust_block = f"""
CUSTOMER (message goes TO this person):
- Name: {cname}
- State: {state}
- Visits total: {rel.get('visits_total', 'N/A')}
- Last visit: {rel.get('last_visit', 'N/A')}
- Services received: {rel.get('services_received', [])}
- Preferred slot: {prefs.get('preferred_slots', 'weekday_evening')}
- Channel: {prefs.get('channel', 'whatsapp')}
- Language preference: {prefs.get('language', 'en')}
- Consent scope: {customer.get('consent', {}).get('scope', [])}"""

    return f"""
MERCHANT:
- Name: {identity.get('name', 'Merchant')}
- Owner first name: {identity.get('owner_first_name', '')}
- City / Locality: {identity.get('locality', '')}, {identity.get('city', '')}
- Verified: {identity.get('verified', False)}
- Languages: {identity.get('languages', ['en'])}
- Subscription: {subs.get('plan', 'Pro')}, {subs.get('days_remaining', 'N/A')} days remaining

PERFORMANCE (30 days):
- Views: {perf.get('views', 0)}, Calls: {perf.get('calls', 0)}, Directions: {perf.get('directions', 0)}
- {ctr_note}
- 7-day delta: views {delta.get('views_pct', 0):+.0%}, calls {delta.get('calls_pct', 0):+.0%}

CUSTOMER AGGREGATE:
- Total unique YTD: {cust_agg.get('total_unique_ytd', 'N/A')}
- Lapsed 180d+: {cust_agg.get('lapsed_180d_plus', cust_agg.get('lapsed', 'N/A'))}
- High-risk adult count: {cust_agg.get('high_risk_adult_count', 'N/A')}
- Retention 6mo: {cust_agg.get('retention_6mo_pct', 'N/A')}
- Active members: {cust_agg.get('active_members', cust_agg.get('active_count', 'N/A'))}

SIGNALS: {signals_str}

TRIGGER:
- Kind: {trigger.get('kind', '')}
- Urgency: {trigger.get('urgency', 2)}/5
- Scope: {trigger.get('scope', 'merchant')}
- Suppression key: {trigger.get('suppression_key', '')}
{digest_block}

DO NOT REPEAT THESE RECENT MESSAGES: {last_str}
{cust_block}
""".strip()

# ── Build system prompt per trigger kind ─────────────────────────────
def build_system_prompt(trigger_kind, category_slug, scope, lang_instruction):
    voice_rules = CATEGORY_VOICE.get(category_slug, CATEGORY_VOICE["restaurants"])
    taboos_str  = ", ".join(f'"{t}"' for t in voice_rules["taboos"])
    vocab_str   = ", ".join(voice_rules["vocab"])

    base = f"""You are Vera, magicpin's AI growth assistant for merchants in India.
You write WhatsApp messages to help merchants grow — sent either TO the merchant (as Vera) or TO a customer (as the merchant, on their behalf).

LANGUAGE: {lang_instruction}

CATEGORY VOICE for {category_slug}:
- Tone: {voice_rules['tone']}
- Use vocabulary from: {vocab_str}
- NEVER use: {taboos_str}
- No URLs in the message body (Meta rejects them — hard penalty)

WHAT THE JUDGE SCORES (each /10):
1. Specificity — real numbers, dates, source citations from the context
2. Category fit — correct vocabulary, tone, no overclaiming
3. Merchant fit — owner name, locality, their actual data (CTR, member count, etc.)
4. Trigger relevance — the trigger is THE reason for messaging, make it obvious
5. Engagement compulsion — one strong reason to reply NOW, low-friction CTA

COMPULSION LEVERS (pick 1-2 per message):
- Source citation: "JIDA Oct 2026, p.14" / "DCI circular" / "batch AT2024-1102"
- Merchant-specific anchor: "your 124 high-risk adult patients" / "your 245 active members"
- Reciprocity / effort externalization: "I'll pull it + draft the WhatsApp for you"
- Loss aversion: "you're missing X" / "this window closes Y"
- Curiosity: "worth a 2-min look?" / "want to see who?"
- Single binary commit: "Reply YES" / "Reply 1 for Wed, 2 for Thu"

HARD RULES (any violation is penalized):
- Never fabricate numbers — only cite numbers from the context provided
- Never put URLs in the message body
- Never repeat a message already sent (check DO NOT REPEAT list)
- One CTA maximum per message
- Max ~4 sentences — be concise
- Use owner first name, not generic "Hi there"
"""

    trigger_instructions = {
        "research_digest": """
TASK — RESEARCH DIGEST:
A new research/digest item landed. Your job:
1. Open with: "[Owner], [Source] [issue] landed." — cite source precisely
2. Anchor to THEIR specific patient/customer cohort from customer_aggregate
3. Give the key stat (%, trial size, page number) — specificity is everything here
4. Offer to do the work: "Want me to pull it + draft a patient-ed WhatsApp you can share?"
5. End with source citation: "— [Source] [page ref]"

EXAMPLE OUTPUT SHAPE (do NOT copy — write your own):
"Dr. Meera, JIDA's Oct issue landed. One item relevant to your high-risk adult patients — 2,100-patient trial showed 3-month fluoride recall cuts caries recurrence 38% better than 6-month. Worth a look (2-min abstract). Want me to pull it + draft a patient-ed WhatsApp you can share? — JIDA Oct 2026 p.14"
""",
        "regulation_change": """
TASK — REGULATION / COMPLIANCE CHANGE:
A regulatory update landed. Your job:
1. Flag urgency appropriately (compliance = high urgency, information = medium)
2. Cite the exact circular/batch/regulation reference
3. Connect to their specific affected patient/customer count from context
4. Offer end-to-end workflow: "Want me to draft X + Y?"
""",
        "perf_dip": """
TASK — PERFORMANCE DIP:
The merchant's views/calls dropped. Your job:
1. Name the exact numbers: "Your views are down X% and calls down Y% this week"
2. Compare to peer median (e.g., "peer median is +Z%")
3. Check if this is seasonal — if so, reframe it as normal (builds trust)
4. Offer one concrete fix (flash deal, offer update, content post)
5. Single yes/no CTA: "Should I run [specific offer] at ₹[price] to recover this week?"
""",
        "performance_dip": """
TASK — PERFORMANCE DIP:
The merchant's views/calls dropped. Your job:
1. Name the exact numbers: "Your views are down X% and calls down Y% this week"
2. Compare to peer median
3. Offer one concrete recovery action
4. Single yes/no CTA
""",
        "perf_spike": """
TASK — PERFORMANCE SPIKE:
Views/calls just jumped. Create urgency to capture the moment.
1. "Your [metric] jumped X% this week — [locality] demand is up"
2. Suggest ONE action to convert this traffic (run offer, update listing)
3. Time-bound: "this window won't last past the weekend"
4. Single yes/no CTA
""",
        "recall_due": """
TASK — CUSTOMER RECALL DUE:
A customer's recall window opened. This message goes TO the customer (merchant_on_behalf).
1. Address customer by name warmly, sign off as merchant's business
2. State time since last visit (calculate from last_visit)
3. Offer 2 specific slots with real times (derive from preferred_slots)
4. Include real offer + price from merchant catalog
5. Use hi-en mix if customer prefers Hindi
6. CTA: "Reply 1 for [slot A], 2 for [slot B]"

EXAMPLE SHAPE:
"Hi Priya, Dr. Meera's clinic here 🦷 It's been 5 months since your last visit — your 6-month cleaning recall is due. Apke liye 2 slots ready hain: Wed 5 Nov, 6pm ya Thu 6 Nov, 5pm. ₹299 cleaning + complimentary fluoride. Reply 1 for Wed, 2 for Thu, or tell us a time that works."
""",
        "customer_lapsed_soft": """
TASK — CUSTOMER LAPSED (SOFT):
A customer hasn't returned in a while. This message goes TO the customer (merchant_on_behalf).
1. Warm, no-shame opening — "happens to most of us"
2. Address by name, sign as owner first name + business name
3. Reference their past service/goal (from services_received or relationship)
4. Offer something specific (new class/service/offer matching their goal)
5. No-commitment CTA: "Reply YES — no commitment"
""",
        "customer_lapsed_hard": """
TASK — CUSTOMER LAPSED (HARD, 60+ days):
1. Even warmer, no-judgment tone
2. Reference specific time elapsed
3. Offer a low-barrier re-entry (free trial spot, first-session discount)
4. Explicit no-commitment reassurance
""",
        "festival_upcoming": """
TASK — FESTIVAL UPCOMING:
1. Name the festival + exact days away
2. Suggest a specific campaign using their active offer
3. Add social proof if available: "X merchants in your area are running campaigns"
4. CTA: "Should I set this up for you? — live in 10 min"
""",
        "seasonal_perf_dip": """
TASK — SEASONAL DIP (expected):
This dip is NORMAL. Build trust by reframing.
1. Acknowledge the dip with exact numbers
2. Explain it's normal: "every [category] in [region] sees -X to -Y% in this window"
3. Recommend the RIGHT action for this season (save ad spend, focus retention)
4. Offer a retention play instead of acquisition
""",
        "supply_alert": """
TASK — SUPPLY / COMPLIANCE ALERT:
Urgent compliance action needed.
1. "Urgent:" opener with batch/circular reference
2. Count of affected customers from customer_aggregate
3. Risk framing: accurate but not alarming ("sub-potency, no safety risk")
4. Offer complete workflow: "Want me to draft their WhatsApp note + the replacement-pickup workflow?"
""",
        "chronic_refill_due": """
TASK — CHRONIC REFILL DUE:
Customer's medicines are running out. Message goes TO customer (or their proxy).
1. Name the medicines specifically (molecule names)
2. State exact refill date
3. Include price + savings clearly
4. Two-channel CTA: "Reply CONFIRM to dispatch, or call [number] for changes"
""",
        "dormant_with_vera": """
TASK — MERCHANT DORMANT (14+ days no reply):
Do NOT re-pitch. Use a curiosity/insight angle.
1. Ask one smart question about their business this week
2. Offer to turn their answer into something useful (Google post, WhatsApp reply)
3. Estimate effort: "Takes 5 min"
4. CTA: one open question
""",
        "merchant_reply": """
TASK — MERCHANT REPLIED:
Respond naturally to continue the conversation.
1. Acknowledge what they said
2. Advance toward the next concrete action
3. Keep it short — they're busy
""",
        "bridal_followup": """
TASK — BRIDAL FOLLOWUP:
Customer is a bride-to-be. Message goes TO customer (merchant_on_behalf).
1. Count days to wedding (from wedding date)
2. Reference their trial / last session
3. Suggest the next logical step in bridal journey with price
4. Honor preferred slot
5. Single binary CTA
""",
    }

    specific = trigger_instructions.get(
        trigger_kind,
        "\nTASK: Write a relevant, specific message based on the context. Use real numbers from context. One clear CTA.\n"
    )

    scope_note = ""
    if scope == "customer":
        scope_note = "\nIMPORTANT: This message goes FROM the merchant TO a customer. Sign off as the merchant's business. Address the customer by name.\n"

    return base + specific + scope_note + "\nWrite ONLY the final message. No intro, no explanation, no quotes around it."

# ── Call Gemini LLM ───────────────────────────────────────────────────
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

# def call_llm(system_prompt, context_block):

#     print(f"DEBUG: GEMINI_API_KEY length = {len(GEMINI_API_KEY)}, prefix = {GEMINI_API_KEY[:8] if GEMINI_API_KEY else 'EMPTY'}")
#     full_prompt = (
#         f"{system_prompt}\n\n"
#         f"---\nCONTEXT (use ALL numbers below — do not invent):\n{context_block}\n---\n"
#         f"Write the message now:"
#     )

#     for model in GEMINI_MODELS:
#         for attempt in range(2):
#             try:
#                 response = requests.post(
#                     f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}",
#                     headers={"Content-Type": "application/json"},
#                     json={"contents": [{"parts": [{"text": full_prompt}]}],
#                           "generationConfig": {"maxOutputTokens": 600, "temperature": 0.7}},
#                     timeout=25
#                 )
#                 data = response.json()

#                 if data.get("error", {}).get("status") == "RESOURCE_EXHAUSTED":
#                     wait = 20 * (attempt + 1)
#                     print(f"Rate limited on {model}. Waiting {wait}s...")
#                     time.sleep(wait)
#                     continue

#                 if data.get("error", {}).get("code") in [404, 400]:
#                     print(f"Model {model} error: {data.get('error', {}).get('message', '')}")
#                     print(f"FULL ERROR: {json.dumps(data)}")
#                     break

#                 text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
#                 print(f"GEMINI SUCCESS: model={model}, text_preview={text[:80]}")
#                 # Remove any accidental URL patterns
#                 text = re.sub(r'https?://\S+', '', text).strip()
#                 return text

#             except Exception as e:
#                 print(f"LLM error with {model}: {e}")
#                 break

#     print(f"ALL GEMINI MODELS FAILED — using fallback")
#     return None

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def call_llm(system_prompt, context_block):
    full_prompt = f"{context_block}\n\nWrite the message now:"
    
    # Try Anthropic first (higher rate limits)
    if ANTHROPIC_API_KEY:
        try:
            print("Calling Anthropic...")
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 500,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": full_prompt}]
                },
                timeout=25
            )
            data = r.json()
            text = data["content"][0]["text"].strip()
            text = re.sub(r'https?://\S+', '', text).strip()
            print(f"ANTHROPIC SUCCESS: {text[:80]}")
            return text
        except Exception as e:
            print(f"Anthropic error: {e}")

    # Fallback to Gemini
    for model in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": f"{system_prompt}\n\n{full_prompt}"}]}],
                      "generationConfig": {"maxOutputTokens": 500, "temperature": 0.7}},
                timeout=25
            )
            data = r.json()
            if data.get("error"):
                print(f"Gemini {model} error: {data['error']}")
                continue
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = re.sub(r'https?://\S+', '', text).strip()
            print(f"GEMINI SUCCESS {model}: {text[:80]}")
            return text
        except Exception as e:
            print(f"Gemini {model} error: {e}")
            continue

    print("ALL LLM FAILED — using fallback")
    return None

# ── Build smart fallback without LLM ─────────────────────────────────
def build_fallback(trigger_kind, category_slug, merchant, trigger, customer=None, offer=None):
    identity  = merchant.get("identity", {})
    perf      = merchant.get("performance", {})
    cust_agg  = merchant.get("customer_aggregate", {})
    locality  = identity.get("locality", "your area")
    owner     = identity.get("owner_first_name", "")
    biz_name  = identity.get("name", "your business")
    views     = perf.get("views", 0)
    delta     = perf.get("delta_7d", {})
    payload   = trigger.get("payload", {})
    m_ctr     = perf.get("ctr", 0)
    p_ctr     = get_peer_stats(category_slug).get("avg_ctr", 0.03)

    o_name  = offer_name(offer) if offer else "Special Offer"
    o_price = offer_price(offer) if offer else "299"

    # Address
    if category_slug == "dentists" and owner:
        addr = f"Dr. {owner}" if not owner.lower().startswith("dr") else owner
    else:
        addr = owner or biz_name

    # 1. Research digest
    if trigger_kind == "research_digest":
        digest = get_digest_item(category_slug, payload.get("top_item_id", ""))
        if digest:
            title   = digest.get("title", "new research")
            source  = digest.get("source", "")
            trial   = digest.get("trial_n", "")
            cohort  = cust_agg.get("high_risk_adult_count", "")
            trial_s = f"{trial}-patient trial showed " if trial else ""
            cohort_s = f" relevant to your {cohort} high-risk patients —" if cohort else " —"
            source_s = f" — {source}" if source else ""
            return (
                f"{addr}, new research just dropped{cohort_s} "
                f"{trial_s}{title}. "
                f"Want me to pull it + draft a patient WhatsApp you can share?{source_s}"
            )
        return (
            f"{addr}, there's new clinical research relevant to your patients this week. "
            f"Want me to pull the abstract + draft a patient-ed WhatsApp for you?"
        )

    # 2. Perf dip
    if "dip" in trigger_kind:
        vd = round(abs(delta.get("views_pct", 0.15)) * 100)
        cd = round(abs(delta.get("calls_pct", 0.10)) * 100)
        return (
            f"{addr}, your views dropped {vd}% and calls {cd}% this week. "
            f"Should I run your {o_name} at ₹{o_price} to recover this?"
        )

    # 3. Perf spike
    if "spike" in trigger_kind:
        return (
            f"{addr}, your listing views jumped this week in {locality} — "
            f"high demand right now. Should I push your {o_name} at ₹{o_price} to convert them?"
        )

    # 4. Recall due
    if trigger_kind == "recall_due" and customer:
        cname = customer.get("name", "there")
        rel   = customer.get("relationship", {})
        lv    = rel.get("last_visit", "")
        months_s = ""
        if lv:
            try:
                from datetime import date
                months = (date.today() - date.fromisoformat(lv)).days // 30
                months_s = f"It's been {months} months since your last visit — "
            except:
                pass
        return (
            f"Hi {cname}, {biz_name} here 🦷 "
            f"{months_s}your recall is due. "
            f"Book your {o_name} at ₹{o_price}. "
            f"Apke liye slots ready hain — reply 1 for Wed 6pm, 2 for Thu 5pm."
        )

    # 5. Customer lapsed
    if "lapsed" in trigger_kind and customer:
        cname    = customer.get("name", "there")
        rel      = customer.get("relationship", {})
        services = rel.get("services_received", [])
        svc_s    = f" — we remember you loved {services[0]}" if services else ""
        return (
            f"Hi {cname}, {owner or biz_name} here 👋 "
            f"It's been a while{svc_s}. No pressure — "
            f"just wanted to share: {o_name} at ₹{o_price} this week. "
            f"Reply YES — no commitment, no auto-charge."
        )

    # 6. Festival
    if "festival" in trigger_kind:
        festival = payload.get("festival_name", "the upcoming festival")
        days     = payload.get("days_away", "")
        days_s   = f" — {days} days away" if days else ""
        return (
            f"{addr}, {festival}{days_s}. "
            f"Should I set up a {o_name} at ₹{o_price} campaign for you? Live in 10 min."
        )

    # 7. Bridal followup
    if "bridal" in trigger_kind and customer:
        cname   = customer.get("name", "there")
        wdate   = customer.get("wedding_date", "")
        days_s  = ""
        if wdate:
            try:
                from datetime import date
                days = (date.fromisoformat(wdate) - date.today()).days
                days_s = f"{days} days to your wedding — "
            except:
                pass
        return (
            f"Hi {cname} 💍 {owner or biz_name} here. "
            f"{days_s}perfect time to start your bridal prep. "
            f"{o_name} at ₹{o_price} — "
            f"want me to block your preferred slot for next week?"
        )

    # 8. Dormant
    if "dormant" in trigger_kind:
        return (
            f"Hi {addr}! Quick question — what service has been most asked-for "
            f"this week at {biz_name}? "
            f"I'll turn the answer into a Google post + WhatsApp reply you can use. Takes 5 min."
        )

    # Generic fallback
    lost = int(max((p_ctr - m_ctr) * views, 0))
    return (
        f"{addr}, {views} people in {locality} viewed your listing this month. "
        f"CTR {m_ctr:.1%} vs peer {p_ctr:.1%} — ~{lost} missed customers. "
        f"Should I run your {o_name} at ₹{o_price} to convert them?"
    )

# ── Main compose function ─────────────────────────────────────────────
# def compose(category, merchant, trigger, customer=None):
#     cust_lang = (customer or {}).get("preferences", {}).get("language", "en")
#     category_slug = category.get("slug", category.get("category_slug", ""))
#     trigger_kind  = (trigger.get("kind") or "").lower()
#     scope = trigger.get("scope", "merchant")
#     send_as = "merchant_on_behalf" if scope == "customer" else "vera"

#     identity = merchant.get("identity", {})
#     perf     = merchant.get("performance", {})
#     delta    = perf.get("delta_7d", {})
#     cust_agg = merchant.get("customer_aggregate", {})
#     locality = identity.get("locality", "")
#     owner    = identity.get("owner_first_name", "")
#     biz_name = identity.get("name", "")

#     offer    = pick_best_offer(merchant, trigger_kind, category_slug)
#     o_name   = offer_name(offer)
#     o_price  = offer_price(offer)

#     peer     = get_peer_stats(category_slug)
#     peer_ctr = peer.get("avg_ctr", 0.03)

#     # -----------------------------
#     # 🔥 1. RESEARCH DIGEST (HIGH SCORE)
#     # -----------------------------
#     if "research" in trigger_kind:
#         digest = get_digest_item(category_slug, trigger.get("payload", {}).get("top_item_id", ""))

#         if digest:
#             source = digest.get("source", "JIDA")
#             stat   = digest.get("key_stat", "38% reduction")
#             trial  = digest.get("trial_n", "2000")

#             cohort = cust_agg.get("high_risk_adult_count", "")
#             cohort_txt = f" relevant to your {cohort} high-risk patients" if cohort else ""

#             message = (
#                 f"Dr. {owner}, {source} latest issue just dropped. "
#                 f"A {trial}-patient study shows {stat}{cohort_txt}. "
#                 f"This could directly improve preventive outcomes in your clinic. "
#                 f"Want me to pull the summary + draft a patient WhatsApp you can send?"
#             )
        

#             return {
#                 "message": message,
#                 "cta": "yes_no",
#                 "send_as_identity": send_as,
#                 "suppression_key": f"{merchant.get('merchant_id')}:{trigger_kind}",
#                 "rationale": "Research digest → strong specificity + cohort relevance + action"
#             }

#     # -----------------------------
#     # 🔥 2. PERFORMANCE DIP
#     # -----------------------------
#     if "dip" in trigger_kind:
#         views_drop = abs(delta.get("views_pct", -0.2)) * 100
#         calls_drop = abs(delta.get("calls_pct", -0.1)) * 100
#         m_ctr      = perf.get("ctr", 0) * 100
#         p_ctr      = get_peer_stats(category_slug).get("avg_ctr", 0.03) * 100
#         message = (
#             f"{owner}, your views dropped {views_drop:.0f}% and calls {calls_drop:.0f}% this week. "
#             f"CTR is {m_ctr:.1f}% vs {p_ctr:.1f}% peer average — you're losing conversions. "
#             f"Running {o_name} at ₹{o_price} can recover this quickly. "
#             f"Should I activate it now?"
#         )
#         return {
#             "message": message,
#             "cta": "yes_no",
#             "send_as_identity": send_as,
#             "suppression_key": f"{merchant.get('merchant_id')}:{trigger_kind}",
#             "rationale": "Perf dip → numbers + peer comparison + clear recovery action"
#         }

#     # -----------------------------
#     # 🔥 3. PERFORMANCE SPIKE
#     # -----------------------------
#     if "spike" in trigger_kind:
#         views = perf.get("views", 0)

#         message = (
#             f"{owner}, your listing is trending — {views} views this month in {locality}. "
#             f"This demand spike won’t last long. "
#             f"Running {o_name} at ₹{o_price} now can convert this traffic. "
#             f"Should I set it live?"
#         )

#         return {
#             "message": message,
#             "cta": "yes_no",
#             "send_as_identity": send_as,
#             "suppression_key": f"{merchant.get('merchant_id')}:{trigger_kind}",
#             "rationale": "Spike → urgency + conversion capture"
#         }

#     # -----------------------------
#     # 🔥 4. CUSTOMER RECALL (BEST SCORING)
#     # -----------------------------
#     if "recall" in trigger_kind and customer:

#         cname = customer.get("name", "Customer")
#         cust_lang = (customer or {}).get("preferences", {}).get("language", "en")

#         if category_slug == "dentists":
#             message = (
#                 f"Hi {cname}, Dr. Meera’s clinic se bol rahe hain. "
#                 f"Aapka dental check due hai.\n\n"
#                 f"Humne 2 slots block kiye hain — Wed 6pm ya Thu 5pm. "
#                 f"Cleaning @ ₹299.\n\n"
#                 f"Reply 1 ya 2 karke confirm karein."
#             )

#         elif category_slug == "salons":
#             message = (
#                 f"Hi {cname}, aapka next grooming session due hai ✨\n\n"
#                 f"Is week evening slots fast fill ho rahe hain — "
#                 f"Wed 6pm ya Thu 5pm available hai.\n\n"
#                 f"Special offer @ ₹299. Book karna hai?"
#             )

#         elif category_slug == "gyms":
#             message = (
#                 f"Hi {cname}, aapka workout streak break ho gaya hai 💪\n\n"
#                 f"Is week se restart karein? Evening slots open hain.\n\n"
#                 f"Main aapke liye plan set kar du — karein?"
#             )

#         elif category_slug == "restaurants":
#             message = (
#                 f"Hi {cname}, aapka favourite meal miss ho raha hai 😄\n\n"
#                 f"Aaj evening me special offer chal raha hai.\n\n"
#                 f"Table reserve kar du?"
#             )

#         elif category_slug == "pharmacies":
#             message = (
#                 f"Hi {cname}, aapka refill due ho sakta hai.\n\n"
#                 f"Delay se treatment impact ho sakta hai.\n\n"
#                 f"Main aapka order place kar du?"
#            )

#         else:
#             message = f"Hi {cname}, your visit is due. Want me to book it?"

#         return {
#             "message": message,
#             "cta": "open_ended",
#             "send_as_identity": "merchant_on_behalf",
#             "suppression_key": f"{merchant.get('merchant_id')}:recall",
#             "rationale": "Recall → category-specific messaging"
#         }

# # -# 🔥 5. FALLBACK — use smart trigger-specific fallback
#     fallback_msg = build_fallback(trigger_kind, category_slug, merchant, trigger, customer, offer)
#     return {
#         "message":          fallback_msg,
#         "cta":              "open_ended",
#         "send_as_identity": send_as if 'send_as' in dir() else "vera",
#         "suppression_key":  trigger.get("suppression_key", f"{merchant.get('merchant_id')}:{trigger_kind}"),
#         "rationale":        f"Smart fallback: {trigger_kind}, offer={o_name} @ Rs{o_price}"
#     }

def compose(category, merchant, trigger, customer=None):
    category_slug = category.get("slug", category.get("category_slug", ""))
    trigger_kind  = trigger.get("kind", "")
    scope         = trigger.get("scope", "merchant")
    send_as       = "merchant_on_behalf" if scope == "customer" else "vera"

    # Ensure category is in CATEGORIES
    if category_slug not in CATEGORIES and isinstance(category, dict):
        CATEGORIES[category_slug] = category

    offer         = pick_best_offer(merchant, trigger_kind, category_slug)
    lang_instr    = get_language_instruction(merchant, customer)
    system_prompt = build_system_prompt(trigger_kind, category_slug, scope, lang_instr)
    context_block = build_context_block(category_slug, merchant, trigger, customer)

    # Try LLM first
    message = call_llm(system_prompt, context_block)

    # Smart fallback if LLM fails
    if not message:
        message = build_fallback(trigger_kind, category_slug, merchant, trigger, customer, offer)

    identity  = merchant.get("identity", {})
    perf      = merchant.get("performance", {})
    digest    = get_digest_item(category_slug, trigger.get("payload", {}).get("top_item_id", ""))
    peer      = get_peer_stats(category_slug)
    m_ctr     = perf.get("ctr", 0)
    p_ctr     = peer.get("avg_ctr", 0.03)
    o_name    = offer_name(offer)
    o_price   = offer_price(offer)

    rationale = (
        f"Trigger: {trigger_kind} (urgency {trigger.get('urgency', 2)}/5). "
        f"Merchant: {identity.get('name', '')} in {identity.get('locality', '')}. "
        f"CTR: {m_ctr:.1%} vs peer {p_ctr:.1%}. "
        f"Offer: {o_name} @ Rs{o_price}. "
        f"Digest: {digest.get('source', '') + ' — ' + digest.get('title', '')[:50] if digest else 'none'}."
    )

    return {
        "message":          message,
        "cta":              "open_ended",
        "send_as_identity": send_as,
        "suppression_key":  trigger.get("suppression_key",
                            f"{merchant.get('merchant_id', 'unknown')}:{trigger_kind}"),
        "rationale":        rationale
    }
# ── Quick test ────────────────────────────────────────────────────────
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))

    for base in ["expanded", "."]:
        mp = os.path.join(script_dir, base, "merchants.json")
        if not os.path.exists(mp):
            mp = os.path.join(script_dir, "merchants_seed.json")
        if os.path.exists(mp):
            merchants = load_json_list(mp)
            break

    for base in ["expanded", "."]:
        cp = os.path.join(script_dir, base, "customers.json")
        if not os.path.exists(cp):
            cp = os.path.join(script_dir, "customers_seed.json")
        if os.path.exists(cp):
            customers = load_json_list(cp)
            break

    for base in ["expanded", "."]:
        tp = os.path.join(script_dir, base, "triggers.json")
        if not os.path.exists(tp):
            tp = os.path.join(script_dir, "triggers_seed.json")
        if os.path.exists(tp):
            triggers = load_json_list(tp)
            break

    print(f"Merchants: {len(merchants)}, Customers: {len(customers)}, Triggers: {len(triggers)}")
    print("=" * 60)

    merchant = merchants[0]
    customer = customers[0]
    category = {"slug": merchant.get("category_slug", "dentists")}

    print(f"Merchant : {merchant.get('identity', {}).get('name', '?')}")
    print(f"Category : {category['slug']}")

    for i, trigger in enumerate(triggers[:3]):
        print(f"\n{'='*60}")
        print(f"Trigger {i+1}: {trigger.get('kind', '?')}")
        print(f"{'='*60}")
        result = compose(category, merchant, trigger, customer)
        print(f"MESSAGE:\n{result['message']}")
        print(f"\nSEND AS: {result['send_as_identity']}")
        print(f"SUPPRESSION: {result['suppression_key']}")
        print(f"RATIONALE: {result['rationale']}")
        time.sleep(3)


