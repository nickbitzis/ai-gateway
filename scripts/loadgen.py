"""Fire synthetic traffic at the gateway from three teams so the dashboard has a story.

  checkout: bursts 40 requests at a 20 requests-per-minute limit, so about half get 429.
  growth:   asks Opus for long answers on a $0.15 budget, so it runs out and gets 402.
  support:  pastes customer emails, phones, SSNs, and card numbers, so the gateway redacts them.
            It also repeats common questions (cache hits) and tries a model it may not use (403).

Uses only the standard library. Usage: uv run python scripts/loadgen.py --url http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import urllib.error
import urllib.request
from collections import Counter, defaultdict

KEYS = {"checkout": "gw-checkout-7f3a91", "growth": "gw-growth-2c8d40", "support": "gw-support-91be55"}

CHECKOUT_PROMPTS = [
    "Classify this cart abandonment reason: shipping cost too high.",
    "Write a one-line apology for a delayed order.",
    "Suggest three upsell items for a customer buying hiking boots.",
    "Summarize the refund policy in two sentences.",
]
GROWTH_PROMPTS = [
    "Draft a 6-week launch plan for the Lantern analytics add-on, with weekly goals.",
    "Write five subject lines for a win-back email to lapsed Pro accounts.",
    "Outline a pricing page experiment with three variants and a success metric.",
    "Analyze why trial-to-paid conversion dropped from 14% to 11% last quarter.",
]
FIRST = ["Dana", "Luis", "Priya", "Marcus", "Ana", "Tom", "Keiko", "Omar"]
CARDS = ["4111 1111 1111 1111", "5500-0000-0000-0004", "3400 000000 00009"]
SSNS = ["123-45-6789", "219-09-9999", "078-05-1120"]


def support_prompt(i: int, rng: random.Random) -> str:
    name = FIRST[i % len(FIRST)]
    ticket = 4100 + i
    kind = i % 4
    if kind == 0:
        return (
            f"Ticket {ticket}: {name} ({name.lower()}@fabrikam-mail.example) says the invoice is wrong. Draft a reply."
        )
    if kind == 1:
        phone = f"({rng.randint(201, 989)}) 555-01{rng.randint(10, 99)}"
        return f"Ticket {ticket}: {name} called from {phone} about a double charge."
    if kind == 2:
        return f"Ticket {ticket}: verify {name}, SSN {SSNS[i % 3]}, then summarize their open tickets."
    return f"Ticket {ticket}: {name} paid with card {CARDS[i % 3]} and wants a partial refund. Write the refund note."


def post(url: str, key: str | None, payload: dict) -> int:
    req = urllib.request.Request(
        url + "/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", **({"x-gateway-key": key} if key else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def run(url: str, seed: int = 7) -> dict[str, Counter]:
    results: dict[str, Counter] = defaultdict(Counter)
    lock = threading.Lock()

    def record(team, status):
        with lock:
            results[team][status] += 1

    def checkout():
        for i in range(40):
            p = {
                "model": "claude-haiku-5-5",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": CHECKOUT_PROMPTS[i % 4]}],
            }
            record("checkout", post(url, KEYS["checkout"], p))

    def growth():
        for i in range(30):
            p = {
                "model": "claude-opus-5-5",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": f"{GROWTH_PROMPTS[i % 4]} (variant {i})"}],
            }
            record("growth", post(url, KEYS["growth"], p))

    def support():
        local = random.Random(seed + 1)
        for i in range(30):
            text = support_prompt(i % 22, local)  # the last 8 repeat earlier tickets, so they hit the cache
            p = {
                "model": "claude-sonnet-5-5",
                "max_tokens": 300,
                "system": "You are a support agent for Contoso Outdoor. Be brief.",
                "messages": [{"role": "user", "content": text}],
            }
            record("support", post(url, KEYS["support"], p))
        p = {"model": "claude-opus-5-5", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]}
        record("support", post(url, KEYS["support"], p))  # 403: Opus is not on support's allowlist
        record("unknown", post(url, "gw-not-a-real-key", p))  # 401
        record("unknown", post(url, None, p))  # 401

    threads = [threading.Thread(target=f) for f in (checkout, growth, support)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    results = run(args.url.rstrip("/"), args.seed)
    print(f"{'team':<10} " + "  ".join(f"{s:>5}" for s in (200, 401, 402, 403, 429, 502)))
    for team in sorted(results):
        c = results[team]
        print(f"{team:<10} " + "  ".join(f"{c.get(s, 0):>5}" for s in (200, 401, 402, 403, 429, 502)))
    print(f"\nDashboard: {args.url.rstrip('/')}/   Usage JSON: /admin/usage   Metrics: /metrics")


if __name__ == "__main__":
    main()
