import json
from .base import Agent
from src.llm_client import LLMClient

class VerifierAgent(Agent):
    def __init__(self, llm_client: LLMClient):
        super().__init__("Verifier Agent", llm_client)

    def process(self, final_json):
        system_prompt = """You are a Verification Agent. Your job is to validate the final output JSON of a dispute case.

CHECK THE FOLLOWING:
1. case_id is present and is a non-empty string.
2. primary_issue is a valid string (not null).
3. All ID arrays respect limits: order_ids<=5, item_ids<=5, seller_ids<=3, payment_ids<=5, related_order_ids<=5, product_ids<=5, category_names<=5, ranked_causes<=3, responsible_parties<=3, evidence_ids<=20, resolution_actions<=5.
4. All monetary values are rounded to 2 decimal places.
5. confidence is between 0 and 1.
6. evidence_ids follow format: "order:<id>", "item:<id>:<id>", "payment:<id>:<seq>", "seller:<id>", "policy:<code>".
7. Timestamps are in "YYYY-MM-DD HH:MM:SS" format or null.
8. If order has no items: item_total_brl, freight_total_brl, expected_total_brl, difference_brl, reconciled must be null.

INPUT: The complete output JSON.

OUTPUT: Return JSON:
{
  "valid": <bool>,
  "issues": ["<description of issue>", ...]  // empty if valid
}"""

        user_prompt = json.dumps(final_json, indent=2)

        # Call LLM
        response_json = self.call_llm(system_prompt, user_prompt)
        
        return response_json.get("valid", False)
