# Production-Grade Order Automation System

**Shopify → Dynamic Shipping + Tariff Engine → Freightcom → Fulfillment → Klaviyo**

Designed for a DTC fitness equipment business at Gym Army’s scale (thousands of orders/month, Canada + US, multi-SKU configurable products, complex cross-border duties).


## High-Level Architecture

```text
Shopify (Source of Truth for Orders & Catalog)
        ↓ (webhooks + Admin API)
Order Intake & Normalization Service
        ↓
BOM Explosion & Component Resolution
        ↓
Parallel Engines:
   ├─ Physical / Cartonization Engine
   ├─ Customs & Tariff Engine
   └─ Rate Shopping & Margin Engine
        ↓
Quote & Decision Service
        ↓
Freightcom Booking + Label Service
        ↓
Audit / Event Log (immutable)
        ↓
Klaviyo (lifecycle) + Internal Ops Dashboard
```

### Core principles
- Deterministic and auditable (every quote can be replayed from inputs).
- Idempotent (same order always produces the same result).
- Fail-closed on critical guardrails (never silently under-charge duties or over-promise delivery).
- Separation of concerns: catalog, physical, customs, rate, and decision layers are independent and versioned.

## Technical Stack (Production)

```text
Layer                  Technology                              Purpose
--------------------------------------------------------------------------------
Order intake           Shopify Admin API + Webhooks           Real-time order capture
                       (orders/create, orders/updated)
Orchestration          Python (FastAPI or Temporal/Airflow)   Workflow orchestration, retries, observability
Data store             PostgreSQL (primary) + Redis (cache)   SKU master, BOM, rules, audit log
Rules engine           Versioned config (YAML/JSON in DB      Tariffs, shipping rules, surcharges
                       or Git) + Python evaluator
External integrations  Freightcom API, Klaviyo API,           Booking, tracking, notifications
                       (optional) UPS/FedEx rate APIs
Observability          Structured logging + metrics           Variance, failure rates, margin leakage
                       (Prometheus/Grafana or PostHog) + alerting
Deployment             Containerized (Docker) on cloud        Production reliability
                       (AWS/GCP) with CI/CD
```

## Data Model (Production Tables)


### `sku_master`
- Component-level catalog
- Fields: sku_id, weight, dims, HTS, COO, unit_cost, carton_id, primary_material

### `listing_bom`
- Shopify listing → component quantities
- Fields: listing_id, component_sku_id, quantity

### `material_content_232`
- Aluminum/steel content value (USD) and weight per SKU
- This is the ONLY valid base for Section 232 duty
- Fields: sku_id, metal, content_value_usd, content_weight_kg, effective_value_usd_for_calc

### `tariff_rules`
- Versioned rules with effective dates
- Fields: rule_id, program (MFN, S301, S232, IEEPA), hts_or_ch99_code, rate_pct, duty_base (full_entered_value or metal_content_only), applies_to, effective_from, effective_to, refund_route, status

### `carton_profiles`
- Physical packaging data
- Fields: carton_id, dims (L×W×H), gross_weight, dim_weight_divisor

### `rate_calc_inputs`
- Parameters used by formulas
- Fields: param_id, value, unit, source, last_verified

### `shipping_rules`
- Carrier, lane, surcharge, and packaging rules
- Fields: rule_id, carrier, condition, threshold, surcharge_amount or logic

### `audit_log` (immutable)
- Every quote, decision, and booking
- Fields: order_id, timestamp, input_snapshot, calculated_duty, calculated_freight, final_price, decision_reason, freightcom_tracking_id

## Detailed Logic & Parameters


### A. Order Intake & Normalization
- Trigger: Shopify webhook (orders/create or orders/updated)
- Normalize: customer address, line items, discounts, currency
- Idempotency key: Shopify order_id + updated_at
- Guardrail: Reject or flag orders with missing required fields (address, weight-relevant items)

### B. BOM Explosion & Component Resolution
- Map Shopify line items → listing_bom → component SKUs
- Resolve quantities (e.g., NUO80 Pair + Stand = 2× NUO80 + 1× NUOSTAND)
- Guardrail: If BOM is incomplete or SKU missing from sku_master → fail closed and alert ops

### C. Physical / Cartonization Engine
Parameters used:
- Actual net weight from sku_master
- Carton profile (dims + gross weight)
- Dimensional weight divisor (carrier-specific, e.g. 139 or 166)
- Additional handling thresholds (weight > 50 lb, longest side > 48 in, etc.)
- Large package thresholds (L+girth > 130 in)

Logic:
1. Assign cartons based on BOM
2. Compute actual weight and dimensional weight for each carton
3. Billable weight = max(actual, dimensional)
4. Apply surcharges if thresholds exceeded
Guardrail: Never understate billable weight; round up per carrier rules

### D. Customs & Tariff Engine
Parameters used (from tariff_rules + material_content_232 + rate_calc_inputs):
- MFN rate (e.g. 4.6% on full entered value for HTS 9506.91.0030)
- Section 301 (7.5% on full entered value for CN-origin)
- Section 232 (50% or 200% ONLY on metal content value, never full entered value)
- IEEPA reciprocal (20% when applicable)
- Entered value per SKU (from rate_calc_inputs or observed)
- Metal content USD value from material_content_232

Logic (per component, then summed):
1. Determine duty base for each rule (full entered value vs metal content only)
2. Apply rate only if rule is active and applies_to matches
3. Sum applicable duties
4. Record which rules fired and the exact base used
Guardrails:
- Section 232 MUST use metal content value only (hard-coded check)
- Missing 232 content data for a metal-bearing SKU → fail closed
- Effective date check on every rule
- Version the entire tariff rule set so historical quotes remain reproducible

### E. Rate Shopping & Margin Engine
Parameters used:
- Billable weight and carton count from physical engine
- Lane (origin → destination FSA/ZIP)
- Carrier/service (Canpar, Purolator, UPS, GLS, etc.)
- Observed or contracted base rates
- Fuel surcharge percentage
- Accessorials (residential, signature, extended area)
- Desired margin buffer or floor

Logic:
1. Generate candidate rates from multiple carriers/services
2. Select optimal rate based on cost + service level rules
3. Add calculated duty
4. Apply margin buffer
5. Produce customer-facing shipping price
Guardrail: Never quote below expected carrier cost + duty (margin floor)

### F. Quote & Decision Service
- Combines outputs of the three engines
- Returns final shipping price + breakdown
- Writes full input snapshot + calculation trace to audit_log
- Decision options: auto-book, hold for review, or reject

### G. Freightcom Booking + Label Service
- Create shipment via Freightcom API using calculated cartonization and rate
- Retrieve label and tracking number
- Store tracking ID and expected cost in audit_log
- Handle retries and idempotency on booking

### H. Downstream
- Push tracking and order status to Klaviyo for customer notifications
- Emit events for internal ops dashboard and reconciliation layer

## Guardrails (Production)


Fail-closed conditions (system refuses to auto-quote or auto-book):
- Missing or incomplete BOM for any line item
- Missing 232 metal content data when Section 232 applies
- Tariff rule set version mismatch or expired rules
- Billable weight calculation failure
- Rate shopping returns no valid carrier option
- Calculated customer price < expected cost + duty (margin floor breach)
- Shopify order data fails validation

Soft alerts (quote proceeds but flagged):
- High variance between quoted and historical lane rates
- Unusual tariff stack (e.g. 200% 232 triggered)
- Cartonization produces more packages than expected

Observability:
- Every quote is fully replayable from audit_log
- Metrics: quote success rate, average margin, duty variance vs carrier invoices, booking failure rate
- Alerts on unexplained variance > threshold

## End-to-End Example Flow (NUO80 Pair + Stand to US)


1. Shopify order arrives → webhook
2. BOM explosion: 2× NUO80 + 1× NUOSTAND
3. Cartonization: CTN_NUO80 × 2 + CTN_STAND × 1 → billable weights calculated
4. Tariff engine:
   - MFN + Section 301 on full entered value
   - Section 232 only on aluminum content USD from material_content_232
   - Reciprocal if applicable
5. Rate engine selects optimal carrier/service for the lane and weight
6. Final price = freight + duty + margin buffer
7. Audit record written
8. Freightcom booking created → label + tracking
9. Klaviyo event fired
10. Later reconciliation compares quoted vs actual Freightcom invoice

## Scale & Operational Notes


- Handles configurable products (singles, pairs, pairs + stand) without hard-coding
- Versioned rules allow tariff changes (new Section 232 rates, reciprocal updates) without code deploys
- Audit log supports post-entry corrections and carrier invoice disputes
- Designed so the same identity-resolution and variance-attribution primitives used in the broader Rev-Ops reconciliation layer can consume the audit records
- At Gym Army scale the system runs continuously with low latency quotes and full traceability for every cross-border order

This architecture turns a previously manual, error-prone process into a deterministic, auditable, margin-protecting production system.
