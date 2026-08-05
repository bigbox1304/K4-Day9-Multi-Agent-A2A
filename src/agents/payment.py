import json
from .base import Agent
from src.llm_client import LLMClient

class PaymentAgent(Agent):
    def __init__(self, payments_df, llm_client: LLMClient):
        super().__init__("Payment Agent", llm_client)
        self.payments_df = payments_df

    def process(self, claimed_order_id, items_data):
        payments = self.payments_df[self.payments_df['order_id'] == claimed_order_id]
        
        # --- All deterministic ---
        
        # payment_ids
        payment_ids = []
        payment_types_set = []
        seen_types = set()
        payment_total = 0.0
        
        for _, p in payments.iterrows():
            seq = int(p['payment_sequential'])
            payment_ids.append(f"{claimed_order_id}:{seq}")
            pt = p['payment_type']
            if pt not in seen_types:
                seen_types.add(pt)
                payment_types_set.append(pt)
            payment_total += float(p['payment_value'])
        
        payment_ids = payment_ids[:5]
        payment_total = round(payment_total, 2)
        
        # item/freight totals
        if items_data:
            item_total = round(sum(float(item['price']) for item in items_data), 2)
            freight_total = round(sum(float(item['freight_value']) for item in items_data), 2)
            expected_total = round(item_total + freight_total, 2)
            difference = round(payment_total - expected_total, 2)
            reconciled = abs(difference) <= 0.10
        else:
            item_total = None
            freight_total = None
            expected_total = None
            difference = None
            reconciled = None
        
        split_payment = len(payments) >= 2

        result = {
            "payment_ids": payment_ids,
            "payment_types": payment_types_set,
            "currency": "BRL",
            "item_total_brl": item_total,
            "freight_total_brl": freight_total,
            "expected_total_brl": expected_total,
            "payment_total_brl": payment_total,
            "difference_brl": difference,
            "reconciled": reconciled,
            "split_payment": split_payment
        }

        # LLM call for validation only
        system_prompt = """You are a Payment Reconciliation Agent. You receive pre-computed payment data.
Validate the reconciliation and return confirmation.
OUTPUT: {"validation": "ok", "notes": "<any observation>"}
CRITICAL: Output must be a valid JSON object."""

        user_prompt = json.dumps(result, indent=2)
        
        try:
            self.call_llm(system_prompt, user_prompt)
        except Exception:
            pass

        return result
