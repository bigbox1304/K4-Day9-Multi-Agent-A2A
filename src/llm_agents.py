from __future__ import annotations

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from openai import APIStatusError, OpenAI, RateLimitError

from .pipeline import (
    CustomerAgent,
    DataStore,
    DeliveryAgent,
    OrderProductAgent,
    PaymentAgent,
    PolicyAgent,
    VerifierAgent,
)


MODEL_NAME = "qwen3:8b"

POLICY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "primary_issue": {"type": "string", "enum": ["canceled_order_paid", "unavailable_order_paid", "late_delivery_seller", "late_delivery_logistics", "valid_split_payment", "unsupported_late_claim"]},
        "secondary_issues": {"type": "array", "items": {"type": "string", "enum": ["multi_item_order", "multi_seller_order", "split_payment", "repeat_customer", "multiple_categories"]}},
        "case_status": {"type": "string", "enum": ["action_required", "no_action"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "root_cause_code": {"type": "string", "enum": ["SELLER_HANDOFF_AFTER_LIMIT", "CARRIER_DELIVERED_AFTER_ESTIMATE", "ORDER_CANCELED_AFTER_PAYMENT", "ORDER_UNAVAILABLE_AFTER_PAYMENT", "MULTIPLE_PAYMENTS_RECONCILED", "DELIVERY_WITHIN_ESTIMATE"]},
        "responsible_parties": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"party_type": {"type": "string", "enum": ["seller", "platform", "logistics_provider"]}, "party_id": {"type": "string"}}, "required": ["party_type", "party_id"]}},
        "recommended_refund_brl": {"type": "number"},
        "resolution_actions": {"type": "array", "items": {"type": "string", "enum": ["issue_full_refund", "refund_freight", "explain_valid_split_payment", "reject_late_refund", "review_seller_handoff", "review_carrier_delay", "verify_refund_completion", "coordinate_multi_seller_case", "verify_payment_allocation"]}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["primary_issue", "secondary_issues", "case_status", "confidence", "root_cause_code", "responsible_parties", "recommended_refund_brl", "resolution_actions", "evidence_ids"]
}


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

    def json_call(self, role: str, task: str, payload: Dict[str, Any], schema: Dict[str, Any] | None = None) -> Dict[str, Any]:
        system = (
            f"You are the {role} in a multi-agent ecommerce dispute investigation. "
            "Return JSON only. Use only facts present in the supplied payload. "
            "Never invent IDs, timestamps, amounts, events, or evidence. "
            "All numeric calculations must be rounded to 2 decimals."
        )
        if os.getenv("LLM_BASE_URL", "").startswith(("http://localhost", "http://127.0.0.1")):
            system = "/no_think\n" + system
            task = "/no_think\n" + task
        response_format = {"type": "json_object"}
        if schema is not None:
            response_format = {"type": "json_schema", "json_schema": {"name": role, "strict": True, "schema": schema}}
        base_url = os.getenv("LLM_BASE_URL", "")
        if "integrate.api.nvidia.com" in base_url:
            extra_body = {"min_thinking_tokens": 1, "max_thinking_tokens": 2}
        else:
            extra_body = {"reasoning": {"enabled": False}}
        if not (base_url.startswith("http://localhost") or base_url.startswith("http://127.0.0.1") or "integrate.api.nvidia.com" in base_url):
            extra_body["provider"] = {"allow_fallbacks": True}
        request = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "1536")),
            "response_format": response_format,
            "extra_body": extra_body,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": task + "\nPAYLOAD:\n" + json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if base_url.startswith(("http://localhost", "http://127.0.0.1")):
            content = self._ollama_native_call(role, task, payload, schema)
        else:
            response = None
            for attempt in range(5):
                try:
                    response = self.client.chat.completions.create(**request)
                    break
                except RateLimitError:
                    if attempt == 4:
                        raise
                    time.sleep(min(60, 10 * (2 ** attempt)))
                except APIStatusError as exc:
                    if exc.status_code != 429 or attempt == 4:
                        raise
                    time.sleep(min(60, 10 * (2 ** attempt)))
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

    def _ollama_native_call(self, role: str, task: str, payload: Dict[str, Any], schema: Dict[str, Any] | None) -> str:
        base = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        endpoint = base[:-3] + "/api/chat" if base.endswith("/v1") else "http://localhost:11434/api/chat"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "/no_think\nYou are the " + role + ". Return JSON only. Never invent facts."},
                {"role": "user", "content": "/no_think\n" + task + "\nPAYLOAD:\n" + json.dumps(payload, ensure_ascii=False)},
            ],
            "stream": False,
            "think": False,
            "format": schema if schema is not None else "json",
            "options": {"temperature": 0, "num_predict": int(os.getenv("LLM_MAX_TOKENS", "1536"))},
        }
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
        return (data.get("message", {}).get("content") or "{}").strip()


def specialist_prompts() -> Dict[str, str]:
    return {
        "customer_agent": "Return ONLY an object with keys customer_unique_id, related_order_ids (max 5), repeat_customer, evidence_ids. related_order_ids are order IDs only.",
        "order_product_agent": "Return ONLY an object with keys item_ids, seller_ids, product_ids, category_names, multi_item_order, multi_seller_order, multiple_categories, evidence_ids. item_ids MUST be '<order_id>:<order_item_id>' (not bare numbers).",
        "payment_agent": "Return ONLY an object with keys item_total_brl, freight_total_brl, expected_total_brl, payment_total_brl, difference_brl, reconciled, payment_ids, payment_types, split_payment, evidence_ids. payment_ids MUST be '<order_id>:<payment_sequential>'. For no item rows use null for item_total_brl, freight_total_brl, expected_total_brl, difference_brl, reconciled.",
        "delivery_agent": "Return ONLY an object with keys delivered_at, estimated_delivery_at, carrier_handoff_at, delivery_variance_hours, seller_handoff_analysis, late_handoff_seller_ids, evidence_ids. Compute delivery_variance_hours from delivered customer date minus estimated date, in hours rounded to 2 decimals.",
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
        self.customer_tool = CustomerAgent()
        self.order_product_tool = OrderProductAgent(store)
        self.policy_tool = PolicyAgent()

    def _deterministic_agent_results(self, context: Dict[str, Any]):
        """Run data agents locally; CSV facts and arithmetic do not need an LLM."""
        agents = (
            self.customer_tool,
            self.order_product_tool,
            self.payment_tool,
            self.delivery_tool,
        )
        return {agent.name: agent.run(context) for agent in agents}

    def _deterministic_specialists(self, context: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        results = self._deterministic_agent_results(context)
        specialist_results = {}
        for name, result in results.items():
            specialist_results[name] = {**result.facts, "evidence_ids": result.evidence_ids}
        return specialist_results

    def process(self, case: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        order_id = case["customer_request"]["claimed_order_id"]
        context = self.store.context(order_id)
        tool_calculations = {
            "payment": self.payment_tool.run(context).facts,
            "delivery": self.delivery_tool.run(context).facts,
        }
        specialist_payload = {"case_id": case["case_id"], "claimed_order_id": order_id, **context,
                              "auditable_tool_calculations": tool_calculations}
        mode = os.getenv("LLM_ORCHESTRATION_MODE", "fast").strip().lower()
        specialist_results: Dict[str, Dict[str, Any]] = {}
        trace: List[Dict[str, Any]] = []

        if mode == "full":
            prompts = specialist_prompts()
            specialist_workers = max(1, int(os.getenv("SPECIALIST_CONCURRENCY", "2")))
            with ThreadPoolExecutor(max_workers=specialist_workers) as pool:
                futures = {
                    pool.submit(self.backend.json_call, name, prompt, specialist_payload): name
                    for name, prompt in prompts.items()
                }
                for future in as_completed(futures):
                    name = futures[future]
                    specialist_results[name] = future.result()
                    trace.append({"case_id": case["case_id"], "agent": name, "status": "completed", "model": self.backend.model})
        else:
            specialist_results = self._deterministic_specialists(context)
            for name in specialist_results:
                trace.append({"case_id": case["case_id"], "agent": name, "status": "completed", "mode": "deterministic"})

        policy_payload = {"case": case, "source_context": specialist_payload, "specialist_handoffs": specialist_results}
        policy_task = (
            "Apply EC_POLICY_V2 in the README, in its stated priority order. Return JSON with "
            "primary_issue, secondary_issues in policy order, case_status, confidence, "
            "root_cause_code, responsible_parties, recommended_refund_brl, resolution_actions "
            "in policy order, and evidence_ids. Use no evidence except IDs present in source data. "
            "Allowed primary_issue values are exactly: canceled_order_paid, unavailable_order_paid, late_delivery_seller, late_delivery_logistics, valid_split_payment, unsupported_late_claim. "
            "Allowed case_status values are exactly: action_required, no_action. "
            "Allowed secondary issues are exactly: multi_item_order, multi_seller_order, split_payment, repeat_customer, multiple_categories. "
            "Allowed root_cause_code values are exactly: SELLER_HANDOFF_AFTER_LIMIT, CARRIER_DELIVERED_AFTER_ESTIMATE, ORDER_CANCELED_AFTER_PAYMENT, ORDER_UNAVAILABLE_AFTER_PAYMENT, MULTIPLE_PAYMENTS_RECONCILED, DELIVERY_WITHIN_ESTIMATE. "
            "responsible_parties MUST be objects like {party_type:'seller|platform|logistics_provider', party_id:'...'}, never strings. "
            "resolution_actions MUST use only README action names, never natural-language sentences."
        )
        if mode == "fast":
            local_results = self._deterministic_agent_results(context)
            local_policy = self.policy_tool.run(context, local_results)
            policy = {
                "primary_issue": local_policy.facts["primary_issue"],
                "secondary_issues": local_policy.facts["secondary_issues"],
                "case_status": local_policy.facts["case_status"],
                "confidence": local_policy.facts["confidence"],
                "root_cause_code": local_policy.facts["cause"],
                "responsible_parties": local_policy.facts["responsible_parties"],
                "recommended_refund_brl": local_policy.facts["refund"],
                "resolution_actions": local_policy.facts["actions"],
                "evidence_ids": local_policy.evidence_ids,
            }
            trace.append({"case_id": case["case_id"], "agent": "policy_agent", "status": "completed", "mode": "deterministic"})
        else:
            policy = self.backend.json_call("policy_agent", policy_task, policy_payload, POLICY_SCHEMA)
            trace.append({"case_id": case["case_id"], "agent": "policy_agent", "status": "completed", "model": self.backend.model})
        output = self._assemble(case, order_id, specialist_results, policy)

        verify_task = (
            "Verify this proposed output against the supplied source context and EC_POLICY_V2. "
            "Return JSON {valid:boolean, errors:[string], corrected_output:object}. "
            "If valid, corrected_output must equal the proposed output. Correct only factual/schema issues."
        )
        verification = {"valid": True}
        if mode == "full":
            verification = self.backend.json_call("verifier_agent", verify_task, {"source_context": specialist_payload, "proposed_output": output})
            trace.append({"case_id": case["case_id"], "agent": "verifier_agent", "status": "completed", "model": self.backend.model, "valid": verification.get("valid")})
        else:
            trace.append({"case_id": case["case_id"], "agent": "verifier_agent", "status": "completed", "mode": "local", "valid": True})
        if not verification.get("valid"):
            for attempt in range(2):
                repair_task = (
                    "Repair the proposed output using only the source context and verifier errors. "
                    "Return JSON with corrected_output containing the complete final output. "
                    "The corrected output MUST use the exact README schema and these exact enums: "
                    "primary_issue in [canceled_order_paid, unavailable_order_paid, late_delivery_seller, late_delivery_logistics, valid_split_payment, unsupported_late_claim]; "
                    "case_status in [action_required, no_action]; secondary issues in [multi_item_order, multi_seller_order, split_payment, repeat_customer, multiple_categories]; "
                    "root causes in [SELLER_HANDOFF_AFTER_LIMIT, CARRIER_DELIVERED_AFTER_ESTIMATE, ORDER_CANCELED_AFTER_PAYMENT, ORDER_UNAVAILABLE_AFTER_PAYMENT, MULTIPLE_PAYMENTS_RECONCILED, DELIVERY_WITHIN_ESTIMATE]. "
                    "responsible_parties must be objects with party_type and party_id; actions must be README action identifiers, not prose. "
                    "item IDs must be '<order_id>:<order_item_id>'."
                )
                repaired = self.backend.json_call("verifier_repair_agent", repair_task, {
                    "source_context": specialist_payload,
                    "proposed_output": output,
                    "verifier_errors": verification.get("errors") or [],
                })
                output = repaired.get("corrected_output") or output
                trace.append({"case_id": case["case_id"], "agent": "verifier_repair_agent", "status": "completed", "attempt": attempt + 1, "model": self.backend.model})
                if not self.verifier.validate(output, self.store):
                    break
        else:
            output = verification.get("corrected_output") or output
        errors = self.verifier.validate(output, self.store)
        if errors and mode != "full":
            # The local policy agent is the correctness fallback for an LLM
            # that invents an evidence ID or violates an output enum.
            local_results = self._deterministic_agent_results(context)
            local_policy = self.policy_tool.run(context, local_results)
            policy = {
                "primary_issue": local_policy.facts["primary_issue"],
                "secondary_issues": local_policy.facts["secondary_issues"],
                "case_status": local_policy.facts["case_status"],
                "confidence": local_policy.facts["confidence"],
                "root_cause_code": local_policy.facts["cause"],
                "responsible_parties": local_policy.facts["responsible_parties"],
                "recommended_refund_brl": local_policy.facts["refund"],
                "resolution_actions": local_policy.facts["actions"],
                "evidence_ids": local_policy.evidence_ids,
            }
            output = self._assemble(case, order_id, specialist_results, policy)
            trace.append({"case_id": case["case_id"], "agent": "policy_agent", "status": "local_fallback", "errors": errors})
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
