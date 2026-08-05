# Architecture - Multi-Agent E-commerce Dispute Resolution

## 1. System Overview
The system employs a multi-agent architecture to handle e-commerce customer disputes autonomously. Each agent is responsible for a specific domain of data (customers, products, payments, delivery, policy validation) to determine the root cause, responsible party, and recommended refund for 50 cases. 

## 2. Agent Roles and Responsibilities
- **Coordinator Agent**: Receives the case input, initializes all other agents, delegates tasks to them in a pipeline, and gathers their output to assemble the final case JSON. Enforces JSON schema and array limits.
- **Customer Agent**: Queries `olist_customers_dataset` and `olist_orders_dataset` to determine `customer_unique_id` and related orders (if `include_customer_history` is true). 
- **Order & Product Agent**: Queries `olist_order_items_dataset`, `olist_products_dataset`, and category translations. Compiles item lists, seller lists, categories, and flags multi-item, multi-seller, or multi-category properties.
- **Payment Agent**: Aggregates payment rows from `olist_order_payments_dataset`, reconciles the expected total vs actual payments (BRL), and detects split payment usage.
- **Delivery Agent**: Uses `olist_orders_dataset` to compute `delivery_variance_hours` and checks `shipping_limit_date` for seller handoff variance, determining if the delay is caused by the logistics carrier or the seller.
- **Policy Agent**: Receives intermediate context and applies `EC_POLICY_V2` rules (prioritized logic). Assigns primary issue, secondary issues, refund amounts, responsible parties, and resolution actions.
- **Verifier Agent**: Examines the completed JSON case output to confirm array limits (e.g. max 5 order ids) and verifies schema validity before saving to `output/`.

## 3. Data Flow & Handoff
1. **Coordinator** loads the input case (e.g. `EC_001.json`).
2. **Coordinator** hands off the `claimed_order_id` to **Customer Agent**. Output: `customer_unique_id`, `related_order_ids`.
3. **Coordinator** hands off to **Order & Product Agent**. Output: item data, seller lists, categories.
4. **Coordinator** sends item totals to **Payment Agent** for reconciliation. Output: expected vs actual totals, `reconciled` flag.
5. **Coordinator** sends order info to **Delivery Agent** to evaluate seller/carrier latencies.
6. All assembled data goes to the **Policy Agent** to evaluate primary issue, secondary issues, root causes, and actions based on the V2 rules.
7. **Coordinator** generates evidence tags based on outputs, merges the structure.
8. **Verifier Agent** audits the final dictionary.
9. Final result is written to `output/EC_xxx.json`.

## 4. Access Scope
Agents do not share overlapping queries. Data access is strictly segmented by domain (e.g., `Payment Agent` only analyzes payments; `Customer Agent` only looks at customer history). 
This segregation guarantees a true multi-agent implementation where responsibilities are separated.
