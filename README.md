# Cadence

A subscription operations workspace built for the Subscription Management System hackathon brief. A custom animated frontend, a FastAPI API, and persistent SQLite storage run together locally.

## Run

Python 3.11+ is required. From this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765. First launch creates `cadence.db` and seeds the demonstration workspace. The login screen provides Admin, Staff, and Customer demo buttons. All demo accounts use `CadenceDemo!2026`; these are synthetic accounts, not production credentials.

| Role | Email |
| --- | --- |
| Admin | admin@cadence.demo |
| Staff | staff@cadence.demo |
| Customer | olivia@acme.demo |

The admin controls catalog, staff, taxes, and discounts. Staff manage subscriptions, invoices, and payment records. Customers can access only their subscriptions, invoices, payments, and lifecycle events. Customer accounts cannot activate themselves or record payments.

## What stands out

- **Billing rehearsal:** a 0–90 day preview calculated using the same billing rules as invoice generation. Surfaces overdue accounts and ineligible discounts, with expected billing and amounts requiring review.
- **Customer journey:** a subscription lifecycle and event timeline from draft through quotation, confirmation, activation, pause, renewal, and closure.
- **Considered visual design:** original CSS/SVG brand artwork, animated orbital illustration, chart transitions, responsive navigation, keyboard search, focus trapping, and reduced-motion support.
- **Billing correctness:** integer minor units, rounded tax calculation, atomic transactions, a unique subscription-period constraint, idempotent payment references, partial payments, and calendar anchors that survive February.

## Included workflows

Customer signup and sign-in; hashed passwords and expiring HttpOnly sessions; single-use password resets; admin-created staff; products with costs, notes and a priced variant; daily, weekly, monthly and yearly plans; minimum quantities and availability dates; quotation templates and validity; multiple product lines; fixed and percentage discounts with dates, product scope, thresholds and usage caps; invoice drafts, confirmation, cancellation, printing and local email queuing; payment records; MRR, revenue, outstanding and overdue reporting; report date/plan filters; CSV exports.

The first subscription line takes the selected plan's per-seat price, plus the optional product variant surcharge. Additional product lines use their catalog sales price on that plan's billing interval. Taxes apply to the discounted total. Invoice lines and totals are stored at billing time.

## Recurring billing worker

The UI can run billing on demand. To run it periodically in another terminal:

```powershell
.\.venv\Scripts\python billing_worker.py --interval 300
```

For an external scheduler, invoke `billing_worker.py --once`. Both use the same transaction-safe billing service. The worker creates drafts for due periods, never charges a customer. It is not started automatically with the local web server.

## Verify

```powershell
.\.venv\Scripts\python -m unittest test_app -v
```

Tests use separate temporary databases. They cover account isolation, permissions, concurrent billing, invoice/payment idempotency, exact totals, partial/final payment, lifecycle enforcement, reset token reuse, expired quotes, month-end anchors, product-scoped discounts and multiple invoice lines.

## Two-minute demo

1. Enter the Admin demo. Show the live dashboard and revenue overview.
2. Open Billing rehearsal. Change the horizon to 30 days and explain the risk flags.
3. Create a subscription from the Growth launch template, adding an extra product line.
4. Open its customer journey: create quotation, confirm, activate.
5. Run billing, inspect the draft, confirm it, and record an external payment.
6. Switch to the Customer demo to show the scoped portal.

## Current boundaries

This is a local hackathon build, not an audited production billing system. Email is stored in the admin-visible local outbox; no real messages are sent. Payment entry records receipts and does not integrate a payment gateway. SQLite runs on one machine; a production release needs PostgreSQL/migrations, pagination and server-side analytics, provider integrations, authentication abuse protection, removal of seeded demo access, and deployment-specific session/CSRF hardening. `CADENCE_SECURE_COOKIE=1` enables secure cookies behind HTTPS. `CADENCE_DB` overrides the database path. Google Fonts are used when online, with system font fallbacks.

Renewal currently creates a one-year term. A billing pass limits historical catch-up to 400 periods per subscription; the next pass continues from the saved cursor. Taxes are snapshotted per subscription. Catalog entries can be created but editing existing commercial terms/proration is not yet implemented. Invoice email and reset links require a real email provider before external use.

The repository has not been pushed to GitHub; publication requires the owner's approval.
