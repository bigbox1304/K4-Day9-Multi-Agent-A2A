from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from openai import OpenAI

from .pipeline import DataStore, DeliveryAgent, PaymentAgent, VerifierAgent


MODEL_NAME = "qwen/qwen3-8b"


def load_local_env() -> None:
    """Load the project .env without requiring an extra dependency."""
    env_path = __import__("pathlib").Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class LLMBackend:
    """OpenAI-compatible client for a local/sub-10B provider."""

    def __init__(self) -> None:
        load_local_env()
        base_url = os.getenv("LLM_BASE_URL")
        api_key = os.getenv("LLM_API_KEY", "local")
        if not base_url:
            raise RuntimeError(
                "LLM_BASE_URL is required. Configure an OpenAI-compatible local "
                "endpoint serving a model <=10B, e.g. vLLM/LM Studio/Ollama."
            )
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = os.getenv("LLM_MODEL", MODEL_NAME)

    def json_call(self, role: str, task: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        system = (
            f"You are the {role} in a multi-agent ecommerce dispute investigation. "
            "Return JSON only. Use only facts present in the supplied payload. "
            "Never invent IDs, timestamps, amounts, events, or evidence. "
            "All numeric calculations must be rounded to 2 decimals."
        )
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=4096,
            response_format={"type": "json_object"},
            extra_body={"reasoning": {"enabled": False}},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": task + "\nPAYLOAD:\n" + json.dumps(payload, ensure_ascii=False)},
            ],
        )
        content = (response.choices[0].message.content or "{}").strip()
        parsed: Any = None
        candidates = [content]
        if content.startswith("```"):
            lines = content.splitlines()
            candidates.insert(0, "\n".join(lines[1:-1]).strip())
        # Handle <think>...</think>, prose before JSON, and JSON strings.
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            candidates.append(content[start:end + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                for _ in range(2):
                    if not isinstance(parsed, str):
                        break
                    parsed = json.loads(parsed.strip())
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                continue
        preview = content[:800].replace("\n", " ")
        raise ValueError(f"{role} did not return a JSON object. Response preview: {preview}")


def specialist_prompts() -> Dict[str, str]:
    return {
        "customer_agent": "Return customer_unique_id, related_order_ids (max 5), repeat_customer, and evidence_ids.",
        "order_product_agent": "Return item_ids, seller_ids, product_ids, category_names, booleans multi_item_order, multi_seller_order, multiple_categories, and evidence_ids.",
        "payment_agent": "Reconcile payment_value against sum(price)+sum(freight_value). Return item_total_brl, freight_total_brl, expected_total_brl, payment_total_brl, difference_brl, reconciled, payment_ids, payment_types, split_payment, and evidence_ids. For no item rows use null for the three required reconciliation fields.",
        "delivery_agent": "Calculate delivery_variance_hours and each seller handoff variance. Return delivered_at, estimated_delivery_at, carrier_handoff_at, seller_handoff_analysis, late_handoff_seller_ids, and evidence_ids.",
    }


def bounded_list(value: Any, limit: int) -> List[Any]:
    if not isinstance(value, list):
        return []
    result: List[Any] = []
    seen = set()
    for item in value:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else item
        if marker not in seen:
            result.append(item)
            seen.add(marker)
        if len(result) >= limit:
            break
    return result


class LLMCoordinator:
    """True multi-agent orchestration: specialists run independently, then hand off JSON."""

    def __init__(self, store: DataStore, backend: LLMBackend):
        self.store = store
        self.backend = backend
        self.verifier = VerifierAgent()
        # These are data tools: they expose auditable arithmetic to agents but
        # do not classify the case or choose a refund.
        self.payment_tool = PaymentAgent()
        self.delivery_tool = DeliveryAgent()

    def process(self, case: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        order_id = case["customer_request"]["claimed_order_id"]
        context = self.store.context(order_id)
        tool_calculations = {
            "payment": self.payment_tool.run(context).facts,
            "delivery": self.delivery_tool.run(context).facts,
        }
        specialist_payload = {"case_id": case["case_id"], "claimed_order_id": order_id, **context,
                              "auditable_tool_calculations": tool_calculations}
        prompts = specialist_prompts()
        specialist_results: Dict[str, Dict[str, Any]] = {}
        trace: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(self.backend.json_call, name, prompt, specialist_payload): name
                for name, prompt in prompts.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                specialist_results[name] = future.result()
                trace.append({"case_id": case["case_id"], "agent": name, "status": "completed", "model": self.backend.model})

        policy_payload = {"case": case, "source_context": specialist_payload, "specialist_handoffs": specialist_results}
        policy_task = (
            "Apply EC_POLICY_V2 in the README, in its stated priority order. Return JSON with "
            "primary_issue, secondary_issues in policy order, case_status, confidence, "
            "root_cause_code, responsible_parties, recommended_refund_brl, resolution_actions "
            "in policy order, and evidence_ids. Use no evidence except IDs present in source data."
        )
        policy = self.backend.json_call("policy_agent", policy_task, policy_payload)
        trace.append({"case_id": case["case_id"], "agent": "policy_agent", "status": "completed", "model": self.backend.model})
        output = self._assemble(case, order_id, specialist_results, policy)

        verify_task = (
            "Verify this proposed output against the supplied source context and EC_POLICY_V2. "
            "Return JSON {valid:boolean, errors:[string], corrected_output:object}. "
            "If valid, corrected_output must equal the proposed output. Correct only factual/schema issues."
        )
        verification = self.backend.json_call("verifier_agent", verify_task, {"source_context": specialist_payload, "proposed_output": output})
        trace.append({"case_id": case["case_id"], "agent": "verifier_agent", "status": "completed", "model": self.backend.model, "valid": verification.get("valid")})
        if not verification.get("valid"):
            repair_task = (
                "Repair the proposed output using only the source context and verifier errors. "
                "Return JSON with corrected_output containing the complete final output. "
                "Preserve valid fields and fill all calculable fields from auditable_tool_calculations."
            )
            repaired = self.backend.json_call("verifier_repair_agent", repair_task, {
                "source_context": specialist_payload,
                "proposed_output": output,
                "verifier_errors": verification.get("errors") or [],
            })
            output = repaired.get("corrected_output") or output
            trace.append({"case_id": case["case_id"], "agent": "verifier_repair_agent", "status": "completed", "model": self.backend.model})
        else:
            output = verification.get("corrected_output") or output
        errors = self.verifier.validate(output, self.store)
        if errors:
            raise ValueError(f"{case['case_id']} structural validation failed: {errors}")
        return output, trace

    @staticmethod
    def _assemble(case: Dict[str, Any], order_id: str, specialists: Dict[str, Dict[str, Any]], policy: Dict[str, Any]) -> Dict[str, Any]:
        customer = specialists.get("customer_agent", {})
        order = specialists.get("order_product_agent", {})
        payment = specialists.get("payment_agent", {})
        delivery = specialists.get("delivery_agent", {})
        causes = [{"cause_code": policy.get("root_cause_code"), "rank": 1}]
        evidence: List[str] = [f"order:{order_id}"]
        for result in specialists.values():
            evidence.extend(result.get("evidence_ids") or [])
        evidence.extend(policy.get("evidence_ids") or [])
        evidence = list(dict.fromkeys(evidence))[:20]
        return {
            "case_id": case["case_id"],
            "case_assessment": {
                "primary_issue": policy.get("primary_issue"),
                "secondary_issues": bounded_list(policy.get("secondary_issues"), 5),
                "case_status": policy.get("case_status"),
                "confidence": policy.get("confidence", 0),
            },
            "affected_entities": {"order_ids": [order_id], "item_ids": bounded_list(order.get("item_ids"), 5), "seller_ids": bounded_list(order.get("seller_ids"), 3), "payment_ids": bounded_list(payment.get("payment_ids"), 5)},
            "customer_context": {"customer_unique_id": customer.get("customer_unique_id"), "related_order_ids": bounded_list(customer.get("related_order_ids"), 5)},
            "product_context": {"product_ids": bounded_list(order.get("product_ids"), 5), "category_names": bounded_list(order.get("category_names"), 5)},
            "delivery_analysis": {k: delivery.get(k) for k in ("delivered_at", "estimated_delivery_at", "carrier_handoff_at", "delivery_variance_hours", "seller_handoff_analysis", "late_handoff_seller_ids")},
            "payment_reconciliation": {"currency": "BRL", **{k: payment.get(k) for k in ("item_total_brl", "freight_total_brl", "expected_total_brl", "payment_total_brl", "difference_brl", "reconciled", "payment_types")}},
            "root_cause_analysis": {"ranked_causes": bounded_list(causes, 3), "responsible_parties": bounded_list(policy.get("responsible_parties"), 3)},
            "evidence_ids": evidence,
            "financial_resolution": {"currency": "BRL", "recommended_refund_brl": policy.get("recommended_refund_brl", 0)},
            "resolution_actions": bounded_list(policy.get("resolution_actions"), 5),
        }
