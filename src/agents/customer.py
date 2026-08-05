import json
from .base import Agent
from src.llm_client import LLMClient

class CustomerAgent(Agent):
    def __init__(self, customers_df, orders_df, llm_client: LLMClient):
        super().__init__("Customer Agent", llm_client)
        self.customers_df = customers_df
        self.orders_df = orders_df

    def process(self, claimed_order_id, include_history):
        # 1. Deterministic pandas lookup
        order = self.orders_df[self.orders_df['order_id'] == claimed_order_id]
        if order.empty:
            return None
            
        customer_id = order.iloc[0]['customer_id']
        customer = self.customers_df[self.customers_df['customer_id'] == customer_id]
        if customer.empty:
            return None
            
        customer_unique_id = customer.iloc[0]['customer_unique_id']
        
        related_order_ids = []
        if include_history:
            related_customers = self.customers_df[self.customers_df['customer_unique_id'] == customer_unique_id]
            related_c_ids = related_customers['customer_id'].tolist()
            related_orders = self.orders_df[self.orders_df['customer_id'].isin(related_c_ids)]
            all_order_ids = related_orders['order_id'].tolist()
            # Exclude claimed_order_id, max 5
            related_order_ids = [oid for oid in all_order_ids if oid != claimed_order_id][:5]

        result = {
            "customer_unique_id": customer_unique_id,
            "related_order_ids": related_order_ids
        }

        # 2. LLM call for validation/summary only
        system_prompt = """You are a Customer Identity Agent. You receive pre-computed customer data.
Validate the data and return it as-is in JSON format.
OUTPUT: {"customer_unique_id": "<string>", "related_order_ids": ["<id>", ...], "validation": "ok"}
CRITICAL: Output must be a valid JSON object."""

        user_prompt = json.dumps(result, indent=2)
        
        try:
            llm_response = self.call_llm(system_prompt, user_prompt)
            # LLM validation is advisory only, we always use deterministic result
        except Exception:
            pass  # LLM failure doesn't affect output

        return result
