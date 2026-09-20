# TASK: Industrial Safety Equipment & PPE Wholesale Order Fulfillment Agent (UC-0169)

## CONTEXT
Build an agentic order-fulfillment system for a wholesale PPE supplier. Orders arrive with
free-text line items that must be reconciled against a catalog, checked against inventory
across two warehouses, risk-scored, and either auto-dispatched or queued for human review.
Every decision must be traceable and reversible.

## TECH STACK (mandatory)
- Backend: Python (FastAPI)
- Database: SQLite (event-sourced / append-only tables, no destructive updates)
- Frontend: plain HTML + CSS + vanilla JS (no framework), served by FastAPI, calling backend
  via fetch() to JSON endpoints
- LLM calls: use an OpenAI-compatible chat completions endpoint. Read `LLM_API_KEY` and
  `LLM_BASE_URL` from environment variables. Build a single `llm_client.py` wrapper function
  `call_llm(system_prompt, user_prompt, response_format="json")` used everywhere the LLM is
  needed. Never hardcode the key.

## INPUT DATA
Three CSVs in /data:
- ppe_catalog.csv: sku, name, price, in_stock (primary warehouse stock)
- wholesale_orders.csv: order_id, client_name, client_type (first-time/repeat customer),
  submitted_at, line_items_description (free text, comma-separated items like
  "5x Chemical-Resistant Gloves - Nitrile (Pair), 5x Tyvek Coverall - Size XL (Case of 25)")
- alt_warehouse_inventory.csv: sku, alt_stock, ship_cost (per-unit cost to ship from alt warehouse)

Known data quality issues to explicitly handle (do not crash, do not silently ignore — log
as flagged events): negative stock values in alt_warehouse_inventory.csv (data corruption),
SKUs with 0 stock in both warehouses, and catalog names that are near-duplicates of each
other (e.g. multiple Hard Hat variants, Half-Face vs Full-Face Respirator) which make plain
text line items genuinely ambiguous.

## ARCHITECTURE: Hybrid matching + event-sourced ledger

### 1. Parsing
Split each order's line_items_description into individual line items
(quantity + free-text description).

### 2. Reconciliation tool (hybrid, cost-aware)
For each line item:
a) First pass — cheap fuzzy/lexical similarity (e.g. rapidfuzz or difflib) against
   catalog `name` field. Produce top 3 candidate SKUs with similarity scores.
b) If the top candidate's score is above a high-confidence threshold AND clearly beats
   the second candidate by a margin -> auto-accept the match, no LLM call.
c) Otherwise (ambiguous, low confidence, or multiple close candidates) -> call the LLM
   with the line item text + the shortlisted candidates + their full catalog details,
   asking it to pick the best match (or declare "no match") with a confidence score and
   one-sentence justification. Return structured JSON.
Log every reconciliation decision (line item, method used [fuzzy/llm], chosen SKU,
confidence, justification) as an event.

### 3. Inventory check tool
For each reconciled line item, check primary warehouse stock (ppe_catalog.in_stock).
If insufficient, check alt_warehouse_inventory.alt_stock for that SKU.
Treat any negative alt_stock value as "corrupted/unavailable data" — flag it as an event
and treat effective stock as 0 for decision purposes, but keep the raw negative value
visible in the audit trail for a human to see.

### 4. Risk scoring (transparent, config-driven, NOT hardcoded thresholds in logic)
Create a risk_config.json with weighted factors, e.g.:
{
  "ambiguous_match_weight": 30,
  "first_time_customer_weight": 20,
  "backorder_involved_weight": 25,
  "reroute_involved_weight": 15,
  "high_order_value_weight": 10,
  "high_order_value_threshold": 2000,
  "auto_dispatch_max_score": 40
}
Compute a risk score per order as the sum of applicable weights. Orders scoring at or
below auto_dispatch_max_score are "low-risk" and eligible for autonomous dispatch.
Everything else goes to a human review queue. Load this config at runtime so it can be
tuned without code changes (this should be editable from the frontend).

### 5. Decision + action tools (agent calls these explicitly, do not skip to conclusions)
- dispatch_order(order_id, line_items): commits a fulfillment event, decrements primary
  stock in the ledger (do not mutate the CSV — treat it as an in-memory/DB "current
  stock view" derived from the event log)
- reroute_line_item(order_id, sku, qty, reason): commits a reroute event to the alt
  warehouse, decrements alt_stock in the ledger view, records ship_cost incurred
- flag_for_review(order_id, reason): commits a pending-review event, does NOT change stock
- backorder_line_item(order_id, sku, qty): commits a backorder event when neither warehouse
  can fulfill it (do not silently drop the line item)

The agent must call reconciliation and inventory-check tools BEFORE deciding on
dispatch/reroute/backorder/review — never let the LLM jump straight to a final action
without evidence from the tools. Structure this as an explicit multi-step loop in Python
(you can implement this as a simple state machine, does not need a heavyweight agent
framework), not as a single LLM call that pretends to have used tools.

### 6. Event-sourced ledger (SQLite)
Tables (append-only, never UPDATE or DELETE existing rows):
- orders (raw ingested orders)
- reconciliation_events (order_id, line_item_text, method, matched_sku, confidence,
  justification, timestamp)
- inventory_events (order_id, sku, warehouse[primary/alt], qty_checked, qty_available,
  flagged_anomaly boolean, timestamp)
- decision_events (order_id, risk_score, decision[auto_dispatch/reroute/backorder/
  human_review], reasoning, timestamp)
- action_events (order_id, action_type[dispatch/reroute/backorder], sku, qty,
  warehouse, timestamp)
- override_events (order_id, human_user, original_decision, new_decision, reason, timestamp)
Compute "current stock" for any SKU on demand by folding the event log, not by mutating
a stock column, so the whole history is always reconstructable and auditable.

### 7. API endpoints (FastAPI)
- POST /ingest — load and process the 3 CSVs, run the full agent pipeline order by order
- GET /orders — list all orders with their current status and risk score
- GET /orders/{order_id}/trace — full reasoning + event timeline for one order (this
  powers the decision graph UI)
- POST /orders/{order_id}/override — human overrides a decision (approve/reject/
  reassign warehouse), writes an override_event
- GET /config — current risk_config.json
- POST /config — update risk weights/thresholds (writes new config, does not retroactively
  change past decisions)

### 8. Frontend (HTML/CSS/JS, no build tools, no frameworks)
- Orders table: order_id, client, risk score (color-coded low/med/high), decision, status
- Click into an order -> shows a visual timeline/decision graph: parsed line items ->
  reconciliation (with confidence %) -> inventory check per warehouse -> risk score
  breakdown (show which weights fired) -> final action taken
- Explicit "Approve" / "Override" buttons on any order still pending human review
- A small settings panel to view/edit risk_config.json weights live and see how many
  currently-pending orders would flip to auto-dispatch under the new weights (before saving)
- Highlight any anomaly-flagged inventory data (e.g. the negative-stock SKU) distinctly

## DELIVERABLES
1. Clean, runnable Python project structure (main.py, llm_client.py, agent.py, ledger.py,
   models.py, static/index.html, static/app.js, static/style.css, risk_config.json)
2. README with setup steps (env vars needed, how to run, how to point at LLM_BASE_URL)
3. Make sure it runs end-to-end against the 3 provided CSVs with zero manual data cleaning
4. Handle the negative-stock and duplicate-catalog-name edge cases visibly, not silently

## NON-NEGOTIABLE CONSTRAINTS
- No destructive writes to the event tables (append-only)
- No LLM call for line items that a cheap match already resolves confidently (cost control)
- Every autonomous action must be traceable back to a specific reasoning chain a human can review
- Risk thresholds must live in an editable config, not hardcoded if/else chains