import json
import pandas as pd
from .base import Agent
from src.llm_client import LLMClient

class OrderProductAgent(Agent):
    def __init__(self, items_df, products_df, categories_df, llm_client: LLMClient):
        super().__init__("Order & Product Agent", llm_client)
        self.items_df = items_df
        self.products_df = products_df
        self.categories_df = categories_df
        if not categories_df.empty:
            self.cat_map = dict(zip(categories_df['product_category_name'], categories_df['product_category_name_english']))
        else:
            self.cat_map = {}

    def process(self, claimed_order_id):
        items = self.items_df[self.items_df['order_id'] == claimed_order_id]
        
        # --- All deterministic ---
        item_ids = []
        seller_ids_set = []
        product_ids_set = []
        cat_names_set = []
        
        seen_sellers = set()
        seen_products = set()
        seen_cats = set()
        
        for _, row in items.iterrows():
            # item_ids: "<order_id>:<order_item_id>"
            item_ids.append(f"{claimed_order_id}:{int(row['order_item_id'])}")
            
            # unique seller_ids
            sid = row['seller_id']
            if sid not in seen_sellers:
                seen_sellers.add(sid)
                seller_ids_set.append(sid)
            
            # unique product_ids
            pid = row['product_id']
            if pid not in seen_products:
                seen_products.add(pid)
                product_ids_set.append(pid)
            
            # category lookup
            prods = self.products_df[self.products_df['product_id'] == pid]
            if not prods.empty:
                cat_pt = prods.iloc[0]['product_category_name']
                if pd.notna(cat_pt) and cat_pt not in seen_cats:
                    seen_cats.add(cat_pt)
                    cat_en = self.cat_map.get(cat_pt, cat_pt)
                    cat_names_set.append(cat_en)
        
        # Apply limits
        item_ids = item_ids[:5]
        seller_ids_set = seller_ids_set[:3]
        product_ids_set = product_ids_set[:5]
        cat_names_set = cat_names_set[:5]
        
        multi_item_order = len(items) >= 2
        multi_seller_order = len(seen_sellers) >= 2
        multiple_categories = len(seen_cats) >= 2

        result = {
            "item_ids": item_ids,
            "seller_ids": seller_ids_set,
            "product_ids": product_ids_set,
            "category_names": cat_names_set,
            "multi_item_order": multi_item_order,
            "multi_seller_order": multi_seller_order,
            "multiple_categories": multiple_categories,
            "items_data": items.to_dict('records')  # pass through for next agents
        }

        # LLM call for validation/reasoning only
        system_prompt = """You are an Order & Product Analysis Agent. You receive pre-computed order data.
Validate the data and return confirmation in JSON.
OUTPUT: {"validation": "ok", "notes": "<any observation>"}
CRITICAL: Output must be a valid JSON object."""

        clean_result = {k: v for k, v in result.items() if k != "items_data"}
        user_prompt = json.dumps(clean_result, indent=2)
        
        try:
            self.call_llm(system_prompt, user_prompt)
        except Exception:
            pass

        return result
