**Team:** Tanishq
**Bot URL:** https://web-production-ad428.up.railway.app
**Model:** gemini-2.5-flash

## Approach
4-context composer (category, merchant, trigger, customer) with trigger-specific routing, Hinglish support, auto-reply detection, hostile handling, and peer CTR benchmarking.

## Tradeoffs
- Gemini 2.5 Flash for speed and free tier availability
- Smart fallback templates when rate limited
- In-memory context store for fast retrieval
" > README.md

git add README.md
git commit -m "add README"
git push origin master --force
