import os
import pandas as pd
from .base import Agent
from .customer import CustomerAgent
from .order_product import OrderProductAgent
from .payment import PaymentAgent
from .delivery import DeliveryAgent
from .policy import PolicyAgent
from .verifier import VerifierAgent
from src.llm_client import LLMClient

class CoordinatorAgent(Agent):
    def __init__(self, data_dir, llm_client: LLMClient):
        super().__init__("Coordinator Agent", llm_client)
        self.data_dir = data_dir
        
    def load_data(self):
        self.orders = pd.read_csv(os.path.join(self.data_dir, 'olist_orders_dataset.csv'))
        self.customers = pd.read_csv(os.path.join(self.data_dir, 'olist_customers_dataset.csv'))
        self.items = pd.read_csv(os.path.join(self.data_dir, 'olist_order_items_dataset.csv'))
        self.products = pd.read_csv(os.path.join(self.data_dir, 'olist_products_dataset.csv'))
        self.sellers = pd.read_csv(os.path.join(self.data_dir, 'olist_sellers_dataset.csv'))
        self.payments = pd.read_csv(os.path.join(self.data_dir, 'olist_order_payments_dataset.csv'))
        self.categories = pd.read_csv(os.path.join(self.data_dir, 'product_category_name_translation.csv'))
        
        self.cust_agent = CustomerAgent(self.customers, self.orders, self.llm)
        self.prod_agent = OrderProductAgent(self.items, self.products, self.categories, self.llm)
        self.pmt_agent = PaymentAgent(self.payments, self.llm)
        self.del_agent = DeliveryAgent(self.orders, self.llm)
        self.pol_agent = PolicyAgent(self.llm)
        self.ver_agent = VerifierAgent(self.llm)

    def process_case(self, case_json, case_file_name, trace_log):
        case_id = case_json['case_id']
        claimed_order_id = case_json['customer_request']['claimed_order_id']
        include_hist = case_json['investigation_scope'].get('include_customer_history', False)
        
        trace_log.append({"step": "Start", "case": case_id, "agent": self.name})
        
        # 1. Customer Agent
        cust_res = self.cust_agent.process(claimed_order_id, include_hist)
        trace_log.append({"step": "Customer context gathered", "agent": self.cust_agent.name})
        
        # 2. Order & Product Agent
        prod_res = self.prod_agent.process(claimed_order_id)
        trace_log.append({"step": "Product context gathered", "agent": self.prod_agent.name})
        
        # 3. Payment Agent
        pmt_res = self.pmt_agent.process(claimed_order_id, prod_res.get('items_data', []))
        trace_log.append({"step": "Payment reconciled", "agent": self.pmt_agent.name})
        
        # 4. Delivery Agent
        del_res = self.del_agent.process(claimed_order_id, prod_res.get('items_data', []))
        trace_log.append({"step": "Delivery analysis done", "agent": self.del_agent.name})
        
        # 5. Policy Agent
        pol_res = self.pol_agent.process(
            {"order_id": claimed_order_id}, 
            prod_res, pmt_res, del_res, cust_res
        )
        trace_log.append({"step": "Policy applied", "agent": self.pol_agent.name})
        
        # --- Build Evidence (deterministic) ---
        evidence = []
        evidence.append(f"order:{claimed_order_id}")
        for i_id in prod_res.get('item_ids', []):
            evidence.append(f"item:{i_id}")
        for p_id in pmt_res.get('payment_ids', []):
            evidence.append(f"payment:{p_id}")
        for r_p in pol_res.get('responsible_parties', []):
            if r_p.get('party_type') == 'seller':
                evidence.append(f"seller:{r_p['party_id']}")
        if pol_res.get('cause_code'):
            evidence.append(f"policy:{pol_res['cause_code']}")
        evidence = evidence[:20]
        
        # --- Assemble final output ---
        final_output = {
            "case_id": case_id,
            "case_assessment": {
                "primary_issue": pol_res.get('primary_issue'),
                "secondary_issues": pol_res.get('secondary_issues', []),
                "case_status": pol_res.get('case_status'),
                "confidence": pol_res.get('confidence', 0.95)
            },
            "affected_entities": {
                "order_ids": [claimed_order_id],
                "item_ids": prod_res.get('item_ids', []),
                "seller_ids": prod_res.get('seller_ids', []),
                "payment_ids": pmt_res.get('payment_ids', [])
            },
            "customer_context": {
                "customer_unique_id": cust_res.get('customer_unique_id') if cust_res else None,
                "related_order_ids": cust_res.get('related_order_ids', []) if cust_res else []
            },
            "product_context": {
                "product_ids": prod_res.get('product_ids', []),
                "category_names": prod_res.get('category_names', [])
            },
            "delivery_analysis": {
                "delivered_at": del_res.get('delivered_at'),
                "estimated_delivery_at": del_res.get('estimated_delivery_at'),
                "carrier_handoff_at": del_res.get('carrier_handoff_at'),
                "delivery_variance_hours": del_res.get('delivery_variance_hours'),
                "seller_handoff_analysis": del_res.get('seller_handoff_analysis', []),
                "late_handoff_seller_ids": del_res.get('late_handoff_seller_ids', [])
            },
            "payment_reconciliation": {
                "currency": pmt_res.get('currency', 'BRL'),
                "item_total_brl": pmt_res.get('item_total_brl'),
                "freight_total_brl": pmt_res.get('freight_total_brl'),
                "expected_total_brl": pmt_res.get('expected_total_brl'),
                "payment_total_brl": pmt_res.get('payment_total_brl'),
                "difference_brl": pmt_res.get('difference_brl'),
                "reconciled": pmt_res.get('reconciled'),
                "payment_types": pmt_res.get('payment_types', [])
            },
            "root_cause_analysis": {
                "ranked_causes": [{"cause_code": pol_res['cause_code'], "rank": 1}] if pol_res.get('cause_code') else [],
                "responsible_parties": pol_res.get('responsible_parties', [])
            },
            "evidence_ids": evidence,
            "financial_resolution": {
                "currency": "BRL",
                "recommended_refund_brl": pol_res.get('refund_amount', 0.0)
            },
            "resolution_actions": pol_res.get('actions', [])
        }
        
        # 6. Verifier Agent
        valid = self.ver_agent.process(final_output)
        trace_log.append({"step": "Validation done", "valid": valid, "agent": self.ver_agent.name})
        
        return final_output
