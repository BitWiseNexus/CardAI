"""
Seed script — inserts sample credit card data directly into Supabase
and rebuilds the FAISS index. Use this to test the chat pipeline
without depending on Gemini availability for ETL.

Usage:
    python -m scripts.seed_data
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import upsert_cards, get_all_cards
from app.vector_store import build_index
from app.models import CreditCard, RewardMultipliers

SAMPLE_CARDS: list[CreditCard] = [
    CreditCard(
        name="Chase Sapphire Preferred Card",
        issuer="Chase",
        annual_fee=95.0,
        regular_apr_low=21.49,
        regular_apr_high=28.49,
        signup_bonus="Earn 60,000 bonus points after spending $4,000 in the first 3 months",
        signup_bonus_value_usd=750.0,
        reward_multipliers=RewardMultipliers(travel=5.0, dining=3.0, groceries=3.0, gas=1.0, online_shopping=1.0, other=1.0),
        lounge_access=False,
        foreign_transaction_fee=0.0,
        credit_score_required="Excellent",
        source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
        description=(
            "The Chase Sapphire Preferred is a premier travel rewards card ideal for frequent travelers and diners. "
            "It earns 5x points on Chase travel portal bookings and 3x on dining and groceries. "
            "Points transfer 1:1 to 14 airline and hotel partners. No foreign transaction fees make it perfect for international trips."
        ),
    ),
    CreditCard(
        name="Chase Sapphire Reserve",
        issuer="Chase",
        annual_fee=550.0,
        regular_apr_low=22.49,
        regular_apr_high=29.49,
        signup_bonus="Earn 60,000 bonus points after spending $4,000 in the first 3 months",
        signup_bonus_value_usd=900.0,
        reward_multipliers=RewardMultipliers(travel=10.0, dining=3.0, groceries=1.0, gas=1.0, online_shopping=1.0, other=1.0),
        lounge_access=True,
        foreign_transaction_fee=0.0,
        credit_score_required="Excellent",
        source_url="https://creditcards.chase.com/rewards-credit-cards/sapphire/reserve",
        description=(
            "The Chase Sapphire Reserve is a luxury travel credit card with a $300 annual travel credit that effectively reduces the fee. "
            "It includes Priority Pass Select lounge access at 1,300+ airports worldwide, 10x points on Chase travel, and comprehensive travel insurance. "
            "Best for high-spend travelers who value premium perks like concierge service and trip delay protection."
        ),
    ),
    CreditCard(
        name="Chase Freedom Unlimited",
        issuer="Chase",
        annual_fee=0.0,
        regular_apr_low=20.49,
        regular_apr_high=29.24,
        signup_bonus="Earn an additional 1.5% cash back on everything in the first year (up to $300)",
        signup_bonus_value_usd=300.0,
        reward_multipliers=RewardMultipliers(travel=5.0, dining=3.0, groceries=3.0, gas=1.5, online_shopping=1.5, other=1.5),
        lounge_access=False,
        foreign_transaction_fee=3.0,
        credit_score_required="Good",
        source_url="https://creditcards.chase.com/cash-back-credit-cards/freedom/unlimited",
        description=(
            "The Chase Freedom Unlimited is a no-annual-fee cash back card offering a flat 1.5% on all purchases with bonus categories. "
            "It earns 3% on dining and drugstores and 5% on Chase Travel. "
            "Ideal for everyday spenders who want simple, consistent rewards without paying an annual fee. "
            "Note: carries a 3% foreign transaction fee so not recommended for international use."
        ),
    ),
    CreditCard(
        name="Chase Freedom Flex",
        issuer="Chase",
        annual_fee=0.0,
        regular_apr_low=20.49,
        regular_apr_high=29.24,
        signup_bonus="Earn $200 cash bonus after spending $500 in first 3 months",
        signup_bonus_value_usd=200.0,
        reward_multipliers=RewardMultipliers(travel=5.0, dining=3.0, groceries=3.0, gas=1.0, online_shopping=1.0, other=1.0),
        lounge_access=False,
        foreign_transaction_fee=3.0,
        credit_score_required="Good",
        source_url="https://creditcards.chase.com/cash-back-credit-cards/freedom/flex",
        description=(
            "The Chase Freedom Flex offers rotating 5% cash back categories each quarter (up to $1,500 spend) like gas stations, Amazon, or grocery stores. "
            "Earns 3% on dining and drugstores year-round. No annual fee makes it a strong choice for budget-conscious reward maximizers. "
            "Best paired with a Sapphire card to convert cash back to transferable points."
        ),
    ),
    CreditCard(
        name="Chase Ink Business Preferred Credit Card",
        issuer="Chase",
        annual_fee=95.0,
        regular_apr_low=21.24,
        regular_apr_high=26.24,
        signup_bonus="Earn 100,000 bonus points after spending $8,000 in the first 3 months",
        signup_bonus_value_usd=1250.0,
        reward_multipliers=RewardMultipliers(travel=3.0, dining=1.0, groceries=1.0, gas=1.0, online_shopping=3.0, other=1.0),
        lounge_access=False,
        foreign_transaction_fee=0.0,
        credit_score_required="Good",
        source_url="https://creditcards.chase.com/business-credit-cards/ink/preferred",
        description=(
            "The Chase Ink Business Preferred is the best business card for travel rewards with a massive 100,000-point welcome bonus. "
            "Earns 3x on travel, shipping, internet/cable/phone, and advertising purchases up to $150,000 per year. "
            "Includes cell phone protection, trip cancellation insurance, and no foreign transaction fees. "
            "Ideal for small businesses with significant travel and operational expenses."
        ),
    ),
]


def main() -> None:
    print(f"Upserting {len(SAMPLE_CARDS)} sample cards to Supabase...")
    rows = upsert_cards(SAMPLE_CARDS)
    print(f"OK: Upserted {len(rows)} rows")

    print("Fetching all cards from DB to build FAISS index...")
    all_cards = get_all_cards()
    build_index(all_cards)
    print(f"OK: FAISS index built with {len(all_cards)} cards")
    print("\nDone! You can now test the /api/chat endpoint.")


if __name__ == "__main__":
    main()
