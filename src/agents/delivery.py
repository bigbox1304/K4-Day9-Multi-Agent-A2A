import pandas as pd
import json
from datetime import datetime
from .base import Agent
from src.llm_client import LLMClient

class DeliveryAgent(Agent):
    def __init__(self, orders_df, llm_client: LLMClient):
        super().__init__("Delivery Agent", llm_client)
        self.orders_df = orders_df

    def _parse_dt(self, val):
        """Parse a datetime string, return None if NaN/NaT."""
        if pd.isna(val) or val is None:
            return None
        try:
            return datetime.strptime(str(val).strip(), '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            return None

    def _format_dt(self, val):
        """Format datetime back to string for output."""
        if pd.isna(val) or val is None:
            return None
        s = str(val).strip()
        # Ensure format YYYY-MM-DD HH:MM:SS (trim microseconds if any)
        if '.' in s:
            s = s.split('.')[0]
        return s

    def process(self, claimed_order_id, items_data):
        order = self.orders_df[self.orders_df['order_id'] == claimed_order_id]
        if order.empty:
            return None
        row = order.iloc[0]
        
        # --- All deterministic ---
        delivered_at_raw = row['order_delivered_customer_date']
        estimated_at_raw = row['order_estimated_delivery_date']
        carrier_at_raw = row['order_delivered_carrier_date']
        order_status = row['order_status']
        
        delivered_dt = self._parse_dt(delivered_at_raw)
        estimated_dt = self._parse_dt(estimated_at_raw)
        carrier_dt = self._parse_dt(carrier_at_raw)
        
        # delivery_variance_hours
        if delivered_dt and estimated_dt:
            delivery_variance_hours = round((delivered_dt - estimated_dt).total_seconds() / 3600.0, 2)
        else:
            delivery_variance_hours = None
        
        # seller handoff analysis
        seller_handoff_analysis = []
        late_handoff_seller_ids = []
        
        if items_data and carrier_dt:
            # Group by seller, take earliest shipping_limit_date per seller
            seller_limits = {}
            for item in items_data:
                s_id = item['seller_id']
                l_dt_str = item.get('shipping_limit_date')
                l_dt = self._parse_dt(l_dt_str)
                if l_dt is not None:
                    if s_id not in seller_limits or l_dt < seller_limits[s_id]:
                        seller_limits[s_id] = l_dt
            
            for s_id, limit_dt in seller_limits.items():
                handoff_var = round((carrier_dt - limit_dt).total_seconds() / 3600.0, 2)
                late = handoff_var > 0
                seller_handoff_analysis.append({
                    "seller_id": s_id,
                    "shipping_limit_at": limit_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    "handoff_variance_hours": handoff_var,
                    "late_handoff": late
                })
                if late:
                    late_handoff_seller_ids.append(s_id)
        elif items_data and not carrier_dt:
            # carrier hasn't picked up yet — can't calculate handoff variance
            seller_limits = {}
            for item in items_data:
                s_id = item['seller_id']
                l_dt_str = item.get('shipping_limit_date')
                l_dt = self._parse_dt(l_dt_str)
                if l_dt is not None:
                    if s_id not in seller_limits or l_dt < seller_limits[s_id]:
                        seller_limits[s_id] = l_dt
            for s_id, limit_dt in seller_limits.items():
                seller_handoff_analysis.append({
                    "seller_id": s_id,
                    "shipping_limit_at": limit_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    "handoff_variance_hours": None,
                    "late_handoff": False
                })

        result = {
            "delivered_at": self._format_dt(delivered_at_raw),
            "estimated_delivery_at": self._format_dt(estimated_at_raw),
            "carrier_handoff_at": self._format_dt(carrier_at_raw),
            "delivery_variance_hours": delivery_variance_hours,
            "seller_handoff_analysis": seller_handoff_analysis,
            "late_handoff_seller_ids": late_handoff_seller_ids,
            "order_status": order_status
        }

        # LLM call for validation only
        system_prompt = """You are a Delivery Analysis Agent. You receive pre-computed delivery data.
Validate the analysis and return confirmation.
OUTPUT: {"validation": "ok", "notes": "<any observation>"}
CRITICAL: Output must be a valid JSON object."""

        user_prompt = json.dumps(result, indent=2)
        
        try:
            self.call_llm(system_prompt, user_prompt)
        except Exception:
            pass

        return result
