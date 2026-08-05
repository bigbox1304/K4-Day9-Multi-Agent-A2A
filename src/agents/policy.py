import json
from .base import Agent
from src.llm_client import LLMClient

class PolicyAgent(Agent):
    def __init__(self, llm_client: LLMClient):
        super().__init__("Policy Agent", llm_client)

    def process(self, order_data, product_data, payment_data, delivery_data, customer_data):
        # --- All deterministic policy logic ---
        
        order_status = delivery_data.get('order_status', '')
        payment_total = payment_data.get('payment_total_brl', 0.0) or 0.0
        delivery_variance = delivery_data.get('delivery_variance_hours')
        late_handoff_sellers = delivery_data.get('late_handoff_seller_ids', [])
        reconciled = payment_data.get('reconciled')
        split_payment = payment_data.get('split_payment', False)
        freight_total = payment_data.get('freight_total_brl') or 0.0
        
        multi_item = product_data.get('multi_item_order', False)
        multi_seller = product_data.get('multi_seller_order', False)
        multiple_cats = product_data.get('multiple_categories', False)
        has_related = len(customer_data.get('related_order_ids', [])) > 0 if customer_data else False
        
        # --- Apply policy table in priority order ---
        primary_issue = None
        responsible_parties = []
        refund_amount = 0.0
        actions = []
        cause_code = None
        
        # 1. canceled_order_paid
        if order_status == 'canceled' and payment_total > 0:
            primary_issue = 'canceled_order_paid'
            responsible_parties = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
            refund_amount = round(payment_total, 2)
            actions.append('issue_full_refund')
            cause_code = 'ORDER_CANCELED_AFTER_PAYMENT'
        
        # 2. unavailable_order_paid
        elif order_status == 'unavailable' and payment_total > 0:
            primary_issue = 'unavailable_order_paid'
            responsible_parties = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
            refund_amount = round(payment_total, 2)
            actions.append('issue_full_refund')
            cause_code = 'ORDER_UNAVAILABLE_AFTER_PAYMENT'
        
        # 3. late_delivery_seller
        elif delivery_variance is not None and delivery_variance > 0 and len(late_handoff_sellers) > 0:
            primary_issue = 'late_delivery_seller'
            for sid in late_handoff_sellers[:3]:
                responsible_parties.append({"party_type": "seller", "party_id": sid})
            refund_amount = round(freight_total, 2)
            actions.append('refund_freight')
            cause_code = 'SELLER_HANDOFF_AFTER_LIMIT'
        
        # 4. late_delivery_logistics
        elif delivery_variance is not None and delivery_variance > 0 and len(late_handoff_sellers) == 0:
            primary_issue = 'late_delivery_logistics'
            responsible_parties = [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]
            refund_amount = round(freight_total, 2)
            actions.append('refund_freight')
            cause_code = 'CARRIER_DELIVERED_AFTER_ESTIMATE'
        
        # 5. valid_split_payment
        elif split_payment and reconciled:
            primary_issue = 'valid_split_payment'
            refund_amount = 0.0
            actions.append('explain_valid_split_payment')
            cause_code = 'MULTIPLE_PAYMENTS_RECONCILED'
        
        # 6. unsupported_late_claim (fallback)
        else:
            primary_issue = 'unsupported_late_claim'
            refund_amount = 0.0
            actions.append('reject_late_refund')
            cause_code = 'DELIVERY_WITHIN_ESTIMATE'
        
        # --- Additional actions (in defined order) ---
        if primary_issue == 'late_delivery_seller':
            actions.append('review_seller_handoff')
        elif primary_issue == 'late_delivery_logistics':
            actions.append('review_carrier_delay')
        
        if refund_amount > 0:
            actions.append('verify_refund_completion')
        
        if multi_seller:
            actions.append('coordinate_multi_seller_case')
        
        if split_payment and primary_issue != 'valid_split_payment':
            actions.append('verify_payment_allocation')
        
        actions = actions[:5]
        
        # --- Secondary issues (in defined order) ---
        secondary_issues = []
        if multi_item:
            secondary_issues.append('multi_item_order')
        if multi_seller:
            secondary_issues.append('multi_seller_order')
        if split_payment:
            secondary_issues.append('split_payment')
        if has_related:
            secondary_issues.append('repeat_customer')
        if multiple_cats:
            secondary_issues.append('multiple_categories')
        
        # --- case_status ---
        case_status = 'action_required' if refund_amount > 0 else 'no_action'
        
        result = {
            "primary_issue": primary_issue,
            "secondary_issues": secondary_issues,
            "case_status": case_status,
            "confidence": 0.95,
            "refund_amount": refund_amount,
            "actions": actions,
            "responsible_parties": responsible_parties[:3],
            "cause_code": cause_code
        }

        # LLM call for reasoning/validation only
        system_prompt = """You are a Policy Agent. You receive a pre-computed policy decision.
Validate the decision and return confirmation.
OUTPUT: {"validation": "ok", "reasoning": "<brief explanation>"}
CRITICAL: Output must be a valid JSON object."""

        user_prompt = json.dumps(result, indent=2)
        
        try:
            self.call_llm(system_prompt, user_prompt)
        except Exception:
            pass

        return result
