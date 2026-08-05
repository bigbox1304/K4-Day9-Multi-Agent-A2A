from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


MODEL_NAME = "qwen3:8b"
POLICY_VERSION = "EC_POLICY_V2"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def money(value: float) -> float:
    return round(float(value) + 1e-9, 2)


def parse_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    return datetime.strptime(value, TIMESTAMP_FORMAT)


def hours_between(later: Optional[datetime], earlier: Optional[datetime]) -> Optional[float]:
    if later is None or earlier is None:
        return None
    return round((later - earlier).total_seconds() / 3600, 2)


def unique_in_order(values: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    seen = set()
    for value in values:
        if value is not None and value != "" and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class DataStore:
    """Loads the Olist data once and exposes indexed, read-only lookups."""

    def __init__(self, data_dir: Path):
        self.orders = read_csv(data_dir / "olist_orders_dataset.csv")
        self.customers = read_csv(data_dir / "olist_customers_dataset.csv")
        self.items = read_csv(data_dir / "olist_order_items_dataset.csv")
        self.payments = read_csv(data_dir / "olist_order_payments_dataset.csv")
        self.products = read_csv(data_dir / "olist_products_dataset.csv")
        self.sellers = read_csv(data_dir / "olist_sellers_dataset.csv")
        self.translations = read_csv(data_dir / "product_category_name_translation.csv")

        self.orders_by_id = {row["order_id"]: row for row in self.orders}
        self.customers_by_id = {row["customer_id"]: row for row in self.customers}
        self.items_by_order = defaultdict(list)
        self.payments_by_order = defaultdict(list)
        self.products_by_id = {row["product_id"]: row for row in self.products}
        self.sellers_by_id = {row["seller_id"]: row for row in self.sellers}
        self.translation_by_category = {
            row["product_category_name"]: row["product_category_name_english"]
            for row in self.translations
        }
        self.orders_by_customer_unique = defaultdict(list)
        self.order_ids_by_customer_id = defaultdict(list)

        for row in self.items:
            self.items_by_order[row["order_id"]].append(row)
        for row in self.payments:
            self.payments_by_order[row["order_id"]].append(row)
        for customer in self.customers:
            self.orders_by_customer_unique[customer["customer_unique_id"]].append(
                customer["customer_id"]
            )
        for order in self.orders:
            self.order_ids_by_customer_id[order.get("customer_id", "")].append(
                order.get("order_id", "")
            )

    def context(self, order_id: str) -> Dict[str, Any]:
        order = self.orders_by_id.get(order_id, {})
        customer = self.customers_by_id.get(order.get("customer_id", ""), {})
        customer_unique_id = customer.get("customer_unique_id", "")
        customer_ids = self.orders_by_customer_unique.get(customer_unique_id, [])
        # The customer table has customer_id, not order_id. Resolve the related orders.
        related_customer_ids = [cid for cid in customer_ids if cid != order.get("customer_id")]
        related_orders = []
        for customer_id in related_customer_ids:
            related_orders.extend(self.order_ids_by_customer_id.get(customer_id, []))
        return {
            "order": order,
            "customer": customer,
            "items": list(self.items_by_order.get(order_id, [])),
            "payments": list(self.payments_by_order.get(order_id, [])),
            "related_order_ids": unique_in_order(related_orders),
        }


@dataclass
class AgentResult:
    agent: str
    facts: Dict[str, Any]
    evidence_ids: List[str]
    warnings: List[str]


class CustomerAgent:
    name = "customer_agent"

    def run(self, context: Dict[str, Any]) -> AgentResult:
        customer = context["customer"]
        order_id = context["order"].get("order_id", "")
        customer_unique_id = customer.get("customer_unique_id") or None
        related = context["related_order_ids"][:5]
        return AgentResult(self.name, {
            "customer_unique_id": customer_unique_id,
            "related_order_ids": related,
            "repeat_customer": bool(related),
        }, [f"order:{order_id}"] if order_id else [], [])


class OrderProductAgent:
    name = "order_product_agent"

    def __init__(self, store: DataStore):
        self.store = store

    def run(self, context: Dict[str, Any]) -> AgentResult:
        order_id = context["order"].get("order_id", "")
        items = context["items"]
        item_ids = [f"{order_id}:{row.get('order_item_id')}" for row in items]
        seller_ids = unique_in_order(row.get("seller_id") for row in items)
        product_ids = unique_in_order(row.get("product_id") for row in items)
        categories = []
        for product_id in product_ids:
            product = self.store.products_by_id.get(product_id, {})
            category = product.get("product_category_name") or None
            if category:
                categories.append(self.store.translation_by_category.get(category, category))
        categories = unique_in_order(categories)
        facts = {
            "item_ids": item_ids[:5], "seller_ids": seller_ids[:3],
            "product_ids": product_ids[:5], "category_names": categories[:5],
            "multi_item_order": len(items) >= 2,
            "multi_seller_order": len(seller_ids) >= 2,
            "multiple_categories": len(categories) >= 2,
        }
        evidence = [f"item:{x}" for x in item_ids[:5]] + [f"seller:{x}" for x in seller_ids[:3]]
        return AgentResult(self.name, facts, evidence, [])


class PaymentAgent:
    name = "payment_agent"

    def run(self, context: Dict[str, Any]) -> AgentResult:
        order_id = context["order"].get("order_id", "")
        items = context["items"]
        payments = context["payments"]
        payment_ids = [f"{order_id}:{row.get('payment_sequential')}" for row in payments]
        payment_total = money(sum(float(row.get("payment_value") or 0) for row in payments))
        payment_types = unique_in_order(row.get("payment_type") for row in payments)
        if not items:
            facts = {"item_total_brl": None, "freight_total_brl": None,
                     "expected_total_brl": None, "payment_total_brl": payment_total,
                     "difference_brl": None, "reconciled": None,
                     "payment_ids": payment_ids[:5], "payment_types": payment_types}
        else:
            item_total = money(sum(float(row.get("price") or 0) for row in items))
            freight_total = money(sum(float(row.get("freight_value") or 0) for row in items))
            expected = money(item_total + freight_total)
            difference = money(payment_total - expected)
            facts = {"item_total_brl": item_total, "freight_total_brl": freight_total,
                     "expected_total_brl": expected, "payment_total_brl": payment_total,
                     "difference_brl": difference, "reconciled": abs(difference) <= 0.10,
                     "payment_ids": payment_ids[:5], "payment_types": payment_types}
        facts["split_payment"] = len(payments) >= 2
        return AgentResult(self.name, facts, [f"payment:{x}" for x in payment_ids[:5]], [])


class DeliveryAgent:
    name = "delivery_agent"

    def run(self, context: Dict[str, Any]) -> AgentResult:
        order = context["order"]
        delivered = parse_time(order.get("order_delivered_customer_date", ""))
        estimated = parse_time(order.get("order_estimated_delivery_date", ""))
        carrier = parse_time(order.get("order_delivered_carrier_date", ""))
        analysis = []
        for item in context["items"]:
            limit = parse_time(item.get("shipping_limit_date", ""))
            variance = hours_between(carrier, limit)
            analysis.append({"seller_id": item.get("seller_id"),
                             "shipping_limit_at": item.get("shipping_limit_date") or None,
                             "handoff_variance_hours": variance,
                             "late_handoff": variance is not None and variance > 0})
        late_sellers = unique_in_order(x["seller_id"] for x in analysis if x["late_handoff"])
        return AgentResult(self.name, {
            "delivered_at": order.get("order_delivered_customer_date") or None,
            "estimated_delivery_at": order.get("order_estimated_delivery_date") or None,
            "carrier_handoff_at": order.get("order_delivered_carrier_date") or None,
            "delivery_variance_hours": hours_between(delivered, estimated),
            "seller_handoff_analysis": analysis,
            "late_handoff_seller_ids": late_sellers,
        }, [], [])


class PolicyAgent:
    name = "policy_agent"

    def run(self, context: Dict[str, Any], results: Dict[str, AgentResult]) -> AgentResult:
        order = context["order"]
        payment = results["payment_agent"].facts
        delivery = results["delivery_agent"].facts
        order_status = order.get("order_status", "")
        paid = payment.get("payment_total_brl", 0) > 0
        late = (delivery.get("delivery_variance_hours") is not None and
                 delivery["delivery_variance_hours"] > 0)
        late_sellers = delivery.get("late_handoff_seller_ids", [])
        reconciled = payment.get("reconciled") is True

        if order_status == "canceled" and paid:
            primary, cause, refund, action = "canceled_order_paid", "ORDER_CANCELED_AFTER_PAYMENT", payment["payment_total_brl"], "issue_full_refund"
            parties = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
        elif order_status == "unavailable" and paid:
            primary, cause, refund, action = "unavailable_order_paid", "ORDER_UNAVAILABLE_AFTER_PAYMENT", payment["payment_total_brl"], "issue_full_refund"
            parties = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
        elif late and late_sellers:
            primary, cause, refund, action = "late_delivery_seller", "SELLER_HANDOFF_AFTER_LIMIT", payment.get("freight_total_brl") or 0, "refund_freight"
            parties = [{"party_type": "seller", "party_id": x} for x in late_sellers[:3]]
        elif late:
            primary, cause, refund, action = "late_delivery_logistics", "CARRIER_DELIVERED_AFTER_ESTIMATE", payment.get("freight_total_brl") or 0, "refund_freight"
            parties = [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]
        elif payment.get("split_payment") and reconciled:
            primary, cause, refund, action = "valid_split_payment", "MULTIPLE_PAYMENTS_RECONCILED", 0, "explain_valid_split_payment"
            parties = []
        elif not late and reconciled:
            primary, cause, refund, action = "unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE", 0, "reject_late_refund"
            parties = []
        else:
            # The assignment taxonomy has no refund branch for incomplete or
            # unreconciled evidence. Keep the case safe and explainable.
            primary, cause, refund, action = "unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE", 0, "reject_late_refund"
            parties = []

        order_result = results["order_product_agent"].facts
        customer_result = results["customer_agent"].facts
        secondary = []
        for key, label in [("multi_item_order", "multi_item_order"), ("multi_seller_order", "multi_seller_order"),
                           ("split_payment", "split_payment"), ("repeat_customer", "repeat_customer"),
                           ("multiple_categories", "multiple_categories")]:
            facts = order_result if key in order_result else payment if key == "split_payment" else customer_result
            if facts.get(key): secondary.append(label)

        actions = [action]
        if primary == "late_delivery_seller": actions.append("review_seller_handoff")
        if primary == "late_delivery_logistics": actions.append("review_carrier_delay")
        if primary in {"canceled_order_paid", "unavailable_order_paid", "late_delivery_seller", "late_delivery_logistics"}: actions.append("verify_refund_completion")
        if order_result.get("multi_seller_order"): actions.append("coordinate_multi_seller_case")
        if payment.get("split_payment") and primary != "valid_split_payment": actions.append("verify_payment_allocation")
        return AgentResult(self.name, {
            "primary_issue": primary, "secondary_issues": secondary,
            "case_status": "action_required" if refund > 0 else "no_action",
            "confidence": 1.0, "cause": cause, "refund": money(refund),
            "responsible_parties": parties, "actions": actions[:5],
        }, [f"policy:{cause}"], [])


class VerifierAgent:
    name = "verifier_agent"

    def validate(self, output: Dict[str, Any], store: DataStore) -> List[str]:
        errors = []
        required = ["case_id", "case_assessment", "affected_entities", "customer_context",
                    "product_context", "delivery_analysis", "payment_reconciliation",
                    "root_cause_analysis", "evidence_ids", "financial_resolution", "resolution_actions"]
        errors.extend(f"missing:{key}" for key in required if key not in output)
        assessment = output.get("case_assessment", {})
        allowed_primary = {"canceled_order_paid", "unavailable_order_paid", "late_delivery_seller", "late_delivery_logistics", "valid_split_payment", "unsupported_late_claim"}
        allowed_secondary = {"multi_item_order", "multi_seller_order", "split_payment", "repeat_customer", "multiple_categories"}
        allowed_status = {"action_required", "no_action"}
        allowed_causes = {"SELLER_HANDOFF_AFTER_LIMIT", "CARRIER_DELIVERED_AFTER_ESTIMATE", "ORDER_CANCELED_AFTER_PAYMENT", "ORDER_UNAVAILABLE_AFTER_PAYMENT", "MULTIPLE_PAYMENTS_RECONCILED", "DELIVERY_WITHIN_ESTIMATE"}
        allowed_actions = {"issue_full_refund", "refund_freight", "explain_valid_split_payment", "reject_late_refund", "review_seller_handoff", "review_carrier_delay", "verify_refund_completion", "coordinate_multi_seller_case", "verify_payment_allocation"}
        if assessment.get("primary_issue") not in allowed_primary:
            errors.append("invalid:primary_issue")
        if assessment.get("case_status") not in allowed_status:
            errors.append("invalid:case_status")
        if not isinstance(assessment.get("secondary_issues"), list) or any(x not in allowed_secondary for x in assessment.get("secondary_issues", [])):
            errors.append("invalid:secondary_issues")
        confidence = assessment.get("confidence")
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            errors.append("confidence_out_of_range")
        for key, limit in [("order_ids", 5), ("item_ids", 5), ("seller_ids", 3), ("payment_ids", 5)]:
            if len(output.get("affected_entities", {}).get(key, [])) > limit:
                errors.append(f"limit:{key}")
        entities = output.get("affected_entities", {})
        case_order_ids = entities.get("order_ids", [])
        if len(case_order_ids) != 1 or not all(x in store.orders_by_id for x in case_order_ids):
            errors.append("invalid:affected_order_ids")
        order_id = case_order_ids[0] if case_order_ids else None
        valid_items = {f"{order_id}:{row.get('order_item_id')}" for row in store.items_by_order.get(order_id, [])} if order_id else set()
        if any(x not in valid_items for x in entities.get("item_ids", [])):
            errors.append("invalid:item_ids")
        valid_sellers = {row.get("seller_id") for row in store.items_by_order.get(order_id, [])} if order_id else set()
        if any(x not in valid_sellers for x in entities.get("seller_ids", [])):
            errors.append("invalid:seller_ids")
        valid_payments = {f"{order_id}:{row.get('payment_sequential')}" for row in store.payments_by_order.get(order_id, [])} if order_id else set()
        if any(x not in valid_payments for x in entities.get("payment_ids", [])):
            errors.append("invalid:payment_ids")
        customer_context = output.get("customer_context", {})
        if customer_context.get("customer_unique_id") and not any(row.get("customer_unique_id") == customer_context.get("customer_unique_id") for row in store.customers):
            errors.append("invalid:customer_unique_id")
        root = output.get("root_cause_analysis", {})
        if not isinstance(root.get("ranked_causes"), list) or any(not isinstance(x, dict) or x.get("cause_code") not in allowed_causes or not isinstance(x.get("rank"), int) for x in root.get("ranked_causes", [])):
            errors.append("invalid:ranked_causes")
        if not isinstance(root.get("responsible_parties"), list) or any(not isinstance(x, dict) or x.get("party_type") not in {"seller", "platform", "logistics_provider"} or not x.get("party_id") for x in root.get("responsible_parties", [])):
            errors.append("invalid:responsible_parties")
        if not isinstance(output.get("resolution_actions"), list) or any(x not in allowed_actions for x in output.get("resolution_actions", [])):
            errors.append("invalid:resolution_actions")
        financial = output.get("financial_resolution", {})
        if financial.get("currency") != "BRL" or not isinstance(financial.get("recommended_refund_brl"), (int, float)):
            errors.append("invalid:financial_resolution")
        payment = output.get("payment_reconciliation", {})
        if payment.get("currency") != "BRL":
            errors.append("invalid:payment_currency")
        evidence_ids = output.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            errors.append("invalid:evidence_ids")
            evidence_ids = []
        if len(evidence_ids) > 20: errors.append("limit:evidence_ids")
        for evidence in evidence_ids:
            if not (evidence.startswith(("order:", "item:", "payment:", "seller:", "policy:"))):
                errors.append(f"invalid_evidence:{evidence}")
        if any(e.startswith("item:") and e not in {f"item:{x}" for x in entities.get("item_ids", [])} for e in evidence_ids):
            errors.append("invalid:item_evidence")
        if any(e.startswith("payment:") and e not in {f"payment:{x}" for x in entities.get("payment_ids", [])} for e in evidence_ids):
            errors.append("invalid:payment_evidence")
        if any(e.startswith("seller:") and e not in {f"seller:{x}" for x in entities.get("seller_ids", [])} for e in evidence_ids):
            errors.append("invalid:seller_evidence")
        for e in evidence_ids:
            if e.startswith("order:") and e != f"order:{order_id}": errors.append("invalid:order_evidence")
            if e.startswith("policy:") and e.split(":", 1)[1] not in allowed_causes: errors.append("invalid:policy_evidence")
        if not output.get("case_id"): errors.append("empty_case_id")
        return errors


class Pipeline:
    def __init__(self, root: Path):
        self.root = root
        self.store = DataStore(root / "data")
        self.customer = CustomerAgent()
        self.order_product = OrderProductAgent(self.store)
        self.payment = PaymentAgent()
        self.delivery = DeliveryAgent()
        self.policy = PolicyAgent()
        self.verifier = VerifierAgent()

    def process(self, case: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        order_id = case["customer_request"]["claimed_order_id"]
        context = self.store.context(order_id)
        results = {
            "customer_agent": self.customer.run(context),
            "order_product_agent": self.order_product.run(context),
            "payment_agent": self.payment.run(context),
            "delivery_agent": self.delivery.run(context),
        }
        policy = self.policy.run(context, results)
        order = context["order"]
        order_result = results["order_product_agent"].facts
        payment_result = results["payment_agent"].facts
        customer_result = results["customer_agent"].facts
        delivery_result = results["delivery_agent"].facts
        evidence = unique_in_order(
            [f"order:{order_id}"] +
            sum((result.evidence_ids for result in results.values()), []) +
            policy.evidence_ids
        )[:20]
        output = {
            "case_id": case["case_id"],
            "case_assessment": {k: policy.facts[k] for k in ("primary_issue", "secondary_issues", "case_status", "confidence")},
            "affected_entities": {"order_ids": [order_id], "item_ids": order_result["item_ids"], "seller_ids": order_result["seller_ids"], "payment_ids": payment_result["payment_ids"]},
            "customer_context": {"customer_unique_id": customer_result["customer_unique_id"], "related_order_ids": customer_result["related_order_ids"]},
            "product_context": {"product_ids": order_result["product_ids"], "category_names": order_result["category_names"]},
            "delivery_analysis": delivery_result,
            "payment_reconciliation": {"currency": "BRL", **{k: payment_result[k] for k in ("item_total_brl", "freight_total_brl", "expected_total_brl", "payment_total_brl", "difference_brl", "reconciled", "payment_types")}},
            "root_cause_analysis": {"ranked_causes": [{"cause_code": policy.facts["cause"], "rank": 1}], "responsible_parties": policy.facts["responsible_parties"]},
            "evidence_ids": evidence,
            "financial_resolution": {"currency": "BRL", "recommended_refund_brl": policy.facts["refund"]},
            "resolution_actions": policy.facts["actions"],
        }
        errors = self.verifier.validate(output, self.store)
        if errors:
            raise ValueError(f"{case['case_id']} verification failed: {errors}")
        trace = [{"case_id": case["case_id"], "agent": result.agent, "status": "completed", "evidence_ids": result.evidence_ids} for result in results.values()]
        trace.append({"case_id": case["case_id"], "agent": "policy_agent", "status": "completed", "primary_issue": policy.facts["primary_issue"]})
        trace.append({"case_id": case["case_id"], "agent": "verifier_agent", "status": "passed"})
        return output, trace


def run(root: Path) -> int:
    input_dir, output_dir = root / "input", root / "output"
    files = sorted(input_dir.glob("EC_*.json"))
    if not files:
        print("No EC_*.json files found in input/. Add the 50 assignment inputs first.")
        return 2
    pipeline = Pipeline(root)
    all_trace = []
    for path in files:
        case = json.loads(path.read_text(encoding="utf-8"))
        output, trace = pipeline.process(case)
        (output_dir / path.name).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        all_trace.extend(trace)
    (root / "trace.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in all_trace) + "\n", encoding="utf-8")
    print(f"Processed {len(files)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(Path(__file__).resolve().parents[1]))
