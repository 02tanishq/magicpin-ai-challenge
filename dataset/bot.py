import json
import os
import requests
import time
from datetime import datetime

# ── API setup ─────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")  # replace this

# ── Load categories once ──────────────────────────────────────────────
def load_categories():
    cats = {}
    for cat_dir in ["expanded/categories", "categories"]:
        if os.path.exists(cat_dir):
            for fname in os.listdir(cat_dir):
                if fname.endswith(".json"):
                    key = fname.replace(".json", "")
                    with open(os.path.join(cat_dir, fname)) as f:
                        cats[key] = json.load(f)
            break  # use first found
    return cats

CATEGORIES = load_categories()

# ── Helper: load json as list ─────────────────────────────────────────
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

# ── Helper: detect auto-reply ─────────────────────────────────────────
AUTO_REPLY_PHRASES = [
    "thank you for contacting",
    "thanks for contacting",
    "we will get back",
    "we'll get back",
    "our team will",
    "please leave a message",
    "currently unavailable",
    "out of office",
    "dhanyavaad",
    "shukriya",
]

def is_auto_reply(message: str) -> bool:
    if not message:
        return False
    msg_lower = message.lower()
    return any(phrase in msg_lower for phrase in AUTO_REPLY_PHRASES)

# ── Helper: detect merchant intent ───────────────────────────────────
INTENT_JOIN = ["join", "judrna", "judna", "sign up", "register",
               "interested", "yes", "haan", "ha ", "let's do", "go ahead",
               "start", "shuru", "karo", "ok", "okay"]

def detect_intent(message: str) -> str:
    if not message:
        return "unknown"
    msg_lower = message.lower()
    if any(phrase in msg_lower for phrase in INTENT_JOIN):
        return "join"
    if any(phrase in msg_lower for phrase in ["stop", "no", "nahi", "band", "not interested"]):
        return "stop"
    return "unknown"

# ── Helper: get best offer ────────────────────────────────────────────
def pick_best_offer(merchant, trigger_kind, category_slug):
    offers = merchant.get("offers", [])
    if offers:
        if trigger_kind in ("perf_dip", "recall_due", "customer_lapsed_soft"):
            discounted = [o for o in offers
                         if o.get("discounted_price") or o.get("discount_pct")]
            if discounted:
                return discounted[0]
        return offers[0]

    fallbacks = {
        "dentists":    {"name": "Dental Cleaning",       "discounted_price": 299},
        "restaurants": {"name": "Special Thali",         "discounted_price": 199},
        "salons":      {"name": "Haircut & Styling",     "discounted_price": 349},
        "gyms":        {"name": "Monthly Membership",    "discounted_price": 999},
        "pharmacies":  {"name": "Health Checkup Package","discounted_price": 499},
    }
    return fallbacks.get(category_slug,
                         {"name": "Special Offer", "discounted_price": 299})

# ── Helper: get peer stats from category ─────────────────────────────
def get_peer_stats(category_slug):
    cat = CATEGORIES.get(category_slug, {})
    return cat.get("peer_stats", {
        "avg_rating": 4.4,
        "avg_reviews": 62,
        "avg_ctr": 0.030
    })

# ── Helper: get digest item from category ────────────────────────────
def get_top_digest(category_slug):
    cat = CATEGORIES.get(category_slug, {})
    digest = cat.get("digest", [])
    if digest:
        return digest[0]
    return None

# ── Helper: get seasonal beat ────────────────────────────────────────
def get_seasonal_beat(category_slug):
    cat = CATEGORIES.get(category_slug, {})
    beats = cat.get("seasonal_beats", [])
    now_month = datetime.now().month
    for beat in beats:
        months_str = beat.get("months", beat.get("month", ""))
        if str(now_month) in str(months_str):
            return beat
    return beats[0] if beats else None

# ── Helper: clean item ID to readable text ───────────────────────────
def clean_item_id(item_id: str) -> str:
    parts = item_id.split("_")
    if len(parts) > 2:
        return " ".join(parts[2:]).replace("-", " ").title()
    return item_id.replace("_", " ").replace("-", " ").title()

# ── Helper: detect language preference ──────────────────────────────
def get_language_instruction(merchant):
    identity = merchant.get("identity", {})
    languages = identity.get("languages", ["en"])
    if "hi" in languages and "en" in languages:
        return "Use a natural Hindi-English mix (Hinglish). Example: 'Dr. Meera, JIDA ka Oct issue aaya — aapke high-risk patients ke liye ek important finding hai.'"
    elif "hi" in languages:
        return "Write primarily in Hindi with key terms in English."
    else:
        return "Write in clear English."

# ── Build rich context string for LLM ───────────────────────────────
def build_context_block(category_slug, merchant, trigger, customer=None):
    identity   = merchant.get("identity", {})
    perf       = merchant.get("performance", {})
    delta      = perf.get("delta_7d", {})
    subs       = merchant.get("subscription", {})
    signals    = merchant.get("signals", [])
    reviews    = merchant.get("review_themes", [])
    cust_agg   = merchant.get("customer_aggregate", {})
    conv_hist  = merchant.get("conversation_history", [])
    peer_stats = get_peer_stats(category_slug)
    digest     = get_top_digest(category_slug)
    seasonal   = get_seasonal_beat(category_slug)
    payload    = trigger.get("payload", {})

    # Performance comparison with peers
    merchant_ctr  = perf.get("ctr", 0)
    peer_ctr      = peer_stats.get("avg_ctr", 0.030)
    ctr_vs_peer   = round((merchant_ctr - peer_ctr) * 100, 1)
    ctr_note      = (f"CTR {merchant_ctr:.1%} is {abs(ctr_vs_peer):.1f}pp "
                     f"{'above' if ctr_vs_peer >= 0 else 'below'} peer median {peer_ctr:.1%}")

    # Recent conversation — anti-repetition
    last_messages = [turn.get("message", "") for turn in conv_hist[-3:]] if conv_hist else []
    last_msg_str  = " | ".join(last_messages) if last_messages else "none"

    # Signals summary
    signals_str = ", ".join(
        s.get("kind", s) if isinstance(s, dict) else str(s)
        for s in signals
    ) or "none"

    # Review themes
    review_str = ", ".join(
        r.get("theme", r) if isinstance(r, dict) else str(r)
        for r in reviews[:3]
    ) or "none"

    # Customer context
    cust_str = ""
    if customer:
        rel   = customer.get("relationship", {})
        prefs = customer.get("preferences", {})
        cust_str = f"""
CUSTOMER:
- Name: {customer.get('name', customer.get('first_name', 'Customer'))}
- State: {customer.get('state', 'unknown')}
- Visits total: {rel.get('visits_total', 'N/A')}
- Last visit: {rel.get('last_visit', 'N/A')}
- Preferred slot: {prefs.get('preferred_slots', 'any')}
- Channel: {prefs.get('channel', 'whatsapp')}
- Language: {prefs.get('language', 'en')}
- Consent scope: {customer.get('consent', {}).get('scope', [])}"""

    # Digest item
    digest_str = ""
    if digest:
        digest_str = f"""
TOP DIGEST ITEM:
- Headline: {digest.get('headline', digest.get('title', 'N/A'))}
- Source: {digest.get('source', 'N/A')}
- Key stat: {digest.get('key_stat', digest.get('summary', 'N/A'))}
- Relevance: {digest.get('relevance', 'high')}"""

    # Seasonal beat
    seasonal_str = ""
    if seasonal:
        seasonal_str = f"\nSEASONAL NOTE: {seasonal.get('note', seasonal.get('beat', ''))}"

    # Trigger payload
    trigger_kind = trigger.get("kind", "")
    top_item_raw = payload.get("top_item_id", payload.get("top_item", ""))
    top_item     = clean_item_id(top_item_raw) if top_item_raw else ""

    return f"""
MERCHANT:
- Name: {identity.get('name', 'Merchant')}
- Owner: {identity.get('owner_first_name', 'there')}
- City/Locality: {identity.get('locality', '')}, {identity.get('city', '')}
- Verified: {identity.get('verified', False)}
- Subscription: {subs.get('plan', 'Pro')}, {subs.get('days_remaining', 'N/A')} days remaining
- Performance (30d): {perf.get('views', 0)} views, {perf.get('calls', 0)} calls, {perf.get('directions', 0)} directions
- CTR: {ctr_note}
- 7d delta: views {delta.get('views_pct', 0):+.0%}, calls {delta.get('calls_pct', 0):+.0%}
- Lapsed customers: {cust_agg.get('lapsed', cust_agg.get('lapsed_count', 78))} (>{cust_agg.get('lapse_days', 180)} days)
- Total unique YTD: {cust_agg.get('total_unique_ytd', 'N/A')}
- Signals: {signals_str}
- Review themes: {review_str}
- Last 3 Vera messages (DO NOT REPEAT): {last_msg_str}

TRIGGER:
- Kind: {trigger_kind}
- Urgency: {trigger.get('urgency', 2)}/5
- Top item: {top_item}
- Scope: {trigger.get('scope', 'merchant')}
{digest_str}
{seasonal_str}
{cust_str}
""".strip()

# ── Build the system prompt per trigger kind ──────────────────────────
def build_system_prompt(trigger_kind, category_slug, scope, language_instruction):
    cat = CATEGORIES.get(category_slug, {})
    voice = cat.get("voice", {})
    taboos = voice.get("taboos", ["guaranteed", "100% safe", "AMAZING"])
    taboos_str = ", ".join(f'"{t}"' for t in taboos[:5])

    base = f"""You are Vera, magicpin's AI growth assistant for merchants.
You compose WhatsApp messages to help merchants grow their business.

LANGUAGE: {language_instruction}

CATEGORY VOICE ({category_slug}):
- Tone: {voice.get('tone', 'professional and helpful')}
- Taboo words (never use): {taboos_str}
- Style: peer voice, not hype. Specific facts over adjectives.

ANTI-PATTERNS (judge will penalize these — avoid):
- Generic offers ("Flat 30% off") — always use service+price ("Dental Cleaning @ ₹299")
- Multiple CTAs — only ONE yes/no action per message
- Long preambles ("I hope you're doing well...")
- Re-introducing yourself after first message
- Promotional tone ("AMAZING DEAL!") for clinical categories
- Hallucinated data — only cite facts given in context
- Repeating the last message verbatim

COMPULSION LEVERS (use 1-2 per message):
- Specificity: real numbers, dates, source citations
- Loss aversion: "you're missing X", "before this window closes"
- Social proof: "3 dentists in your locality did X this month"
- Effort externalization: "I've drafted X — just say go"
- Curiosity: "want to see who?" / "want the full breakdown?"
- Single binary commitment: YES/NO or 1/2 — not multi-choice
"""

    trigger_instructions = {
        "research_digest": """
TRIGGER TASK: A new research/digest item just dropped relevant to this category.
- Lead with the specific finding (stat, trial size, source)
- Connect it to THIS merchant's patient/customer profile
- Offer to do the work for them (draft content, pull abstract)
- CTA: "Want me to pull it + draft a patient-ed WhatsApp you can share?"
""",
        "perf_spike": """
TRIGGER TASK: This merchant's views/calls just spiked.
- Lead with the spike number vs their average
- Create urgency — this window won't last
- Suggest a specific action to capture the traffic (run an offer, update listing)
- CTA: One clear yes/no question
""",
        "perf_dip": """
TRIGGER TASK: This merchant's performance dropped this week.
- Lead with the drop (be specific: views down X%, calls down Y%)
- Compare to peer median to show the gap
- Offer one concrete fix (flash deal, update offer, post content)
- CTA: "Should I run a [specific offer] to recover this week?"
""",
        "recall_due": """
TRIGGER TASK: A customer's recall window has opened (time for their next visit).
- Address the customer by name
- Mention specific time since last visit
- Offer specific available slots with real times
- Use the merchant's actual offer/price
- CTA: "Reply 1 for [slot A], 2 for [slot B]" (booking flows allow multi-choice)
""",
        "customer_lapsed_soft": """
TRIGGER TASK: A customer has lapsed and needs a gentle recall nudge.
- Address the customer by name warmly
- Reference their last visit naturally
- Make returning easy with a specific offer
- CTA: One easy action
""",
        "dormant_with_vera": """
TRIGGER TASK: Merchant hasn't replied to Vera in 14+ days.
- Don't re-pitch — ask a curious question instead
- Use a knowledge/insight angle ("did you know X about your category this week?")
- Make it easy to re-engage with a simple curiosity CTA
- CTA: "Want to see?" or "Worth 2 mins?"
""",
        "review_theme_emerged": """
TRIGGER TASK: A pattern emerged in recent reviews.
- Name the theme (e.g., "wait time" mentioned 3x this week)
- Offer a quick action to address or amplify it
- CTA: One yes/no
""",
        "festival_upcoming": """
TRIGGER TASK: A festival is coming up in a few days.
- Name the festival and exact days away
- Suggest a specific festival campaign with real offer
- Mention how many merchants in their area are running campaigns
- CTA: "Should I set this up for you?"
""",
    }

    specific = trigger_instructions.get(
        trigger_kind,
        "\nTRIGGER TASK: Compose a relevant, specific message based on the context provided.\n"
    )

    if scope == "customer":
        specific += "\nIMPORTANT: This message goes FROM the merchant TO a customer. Use send_as=merchant_on_behalf. Address the customer by name. Sign off as the merchant's clinic/business.\n"

    return base + specific + "\nWrite ONLY the final message. No intro, no explanation, no quotes around it."

# ── Call Gemini LLM ───────────────────────────────────────────────────
def call_llm(system_prompt, context_block):
    full_prompt = f"{system_prompt}\n\n---\nCONTEXT:\n{context_block}\n---\nWrite the message now:"

    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

    for model in models:
        for attempt in range(2):
            try:
                response = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}",
                    headers={"Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": full_prompt}]}]},
                    timeout=25
                )
                data = response.json()

                if data.get("error", {}).get("status") == "RESOURCE_EXHAUSTED":
                    wait = 20 * (attempt + 1)
                    print(f"Rate limited on {model}. Waiting {wait}s...")
                    time.sleep(wait)
                    continue

                if data.get("error", {}).get("code") == 404:
                    print(f"Model {model} not found, trying next...")
                    break

                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                return text

            except Exception as e:
                print(f"Error with {model}: {e}")
                break

    return None

# ── Main compose function ─────────────────────────────────────────────
def compose(category, merchant, trigger, customer=None):
    category_slug = category.get("slug", category.get("category_slug", ""))
    trigger_kind  = trigger.get("kind", "")
    scope         = trigger.get("scope", "merchant")

    # Check for auto-reply in conversation history
    conv_hist = merchant.get("conversation_history", [])
    if conv_hist:
        last_merchant_msg = next(
            (t.get("message", "") for t in reversed(conv_hist)
             if t.get("role") == "merchant"),
            ""
        )
        if is_auto_reply(last_merchant_msg):
            trigger_kind = "auto_reply_detected"

    # Build components
    offer          = pick_best_offer(merchant, trigger_kind, category_slug)
    lang_instr     = get_language_instruction(merchant)
    system_prompt  = build_system_prompt(trigger_kind, category_slug, scope, lang_instr)
    context_block  = build_context_block(category_slug, merchant, trigger, customer)

    # Call LLM
    message = call_llm(system_prompt, context_block)

    # Fallback if LLM fails
    if not message:
        identity = merchant.get("identity", {})
        locality = identity.get("locality", "your area")
        perf     = merchant.get("performance", {})
        views    = perf.get("views", 0)
        o_name   = offer.get("name", "Special Offer") if offer else "Special Offer"
        o_price  = offer.get("discounted_price", "") if offer else ""
        price_str = f"@ ₹{o_price}" if o_price else ""
        message  = (f"{views} people in {locality} viewed your listing this month. "
                    f"Should I run your {o_name} {price_str} to convert them?")

    # Determine send_as
    if scope == "customer":
        send_as = "merchant_on_behalf"
    elif trigger_kind in ("dormant_with_vera", "research_digest"):
        send_as = "vera"
    else:
        send_as = "vera"

    return {
        "message":          message,
        "cta":              message.split("?")[-2].strip() + "?" if "?" in message else message,
        "send_as_identity": send_as,
        "suppression_key":  trigger.get("suppression_key",
                            f"{merchant.get('merchant_id')}:{trigger_kind}"),
        "rationale": (
            f"Trigger: {trigger_kind} (urgency {trigger.get('urgency',2)}). "
            f"Scope: {scope}. "
            f"Offer: {offer.get('name') if offer else 'none'}. "
            f"Lang: {lang_instr[:30]}."
        )
    }

# ── Test all trigger types ────────────────────────────────────────────
if __name__ == "__main__":
    import os

    # ── Use expanded dataset if available, else fall back to seeds ────
# ── Detect correct base path ──────────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))

    if os.path.exists(os.path.join(script_dir, "expanded")):
        base = os.path.join(script_dir, "expanded")
    else:
        base = script_dir

    print(f"Using base path: {base}")

    # Load merchants
    merchants_path = os.path.join(base, "merchants.json")
    if not os.path.exists(merchants_path):
        merchants_path = os.path.join(script_dir, "merchants_seed.json")
    merchants = load_json_list(merchants_path)

    # Load customers
    customers_path = os.path.join(base, "customers.json")
    if not os.path.exists(customers_path):
        customers_path = os.path.join(script_dir, "customers_seed.json")
    customers = load_json_list(customers_path)

    # Load triggers
    triggers_path = os.path.join(base, "triggers.json")
    if not os.path.exists(triggers_path):
        triggers_path = os.path.join(script_dir, "triggers_seed.json")
    triggers = load_json_list(triggers_path)

    # Load categories
    cat_dir = os.path.join(base, "categories")
    if not os.path.exists(cat_dir):
        cat_dir = os.path.join(script_dir, "categories")
    if os.path.exists(cat_dir):
        for fname in os.listdir(cat_dir):
            if fname.endswith(".json"):
                key = fname.replace(".json", "")
                with open(os.path.join(cat_dir, fname)) as f:
                    CATEGORIES[key] = json.load(f)

    print(f"Loaded from: {base}")
    print(f"Merchants: {len(merchants)}")
    print(f"Customers: {len(customers)}")
    print(f"Triggers:  {len(triggers)}")
    print(f"Categories: {list(CATEGORIES.keys())}")
    print("=" * 60)

    merchant = merchants[0]
    customer = customers[0]
    category = {"slug": merchant.get("category_slug", "dentists")}

    print(f"Merchant : {merchant.get('identity',{}).get('name','?')}")
    print(f"Category : {category['slug']}")
    print("=" * 60)

    for i, trigger in enumerate(triggers[:3]):
        print(f"\n--- Trigger {i+1}: {trigger.get('kind','?')} ---")
        result = compose(category, merchant, trigger, customer)
        print(f"MESSAGE:\n{result['message']}")
        print(f"SEND AS: {result['send_as_identity']}")
        print(f"RATIONALE: {result['rationale']}")
        print()
        time.sleep(3)