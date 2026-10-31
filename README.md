# Cadence

**Live demo:** https://cadence-odoo.onrender.com · **Repository branch:** `codex/cadence-build`

A subscription operations workspace built for the Subscription Management System hackathon brief. A custom animated frontend, a FastAPI API, and persistent storage run together: SQLite locally, PostgreSQL on Render.

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

- **Revenue Lab:** an interactive twelve-month pricing, churn and growth simulator. Starts from actual workspace MRR and shows its assumptions explicitly. No AI API or prediction claim.
- **Guided tour:** a three-minute presentation path through billing rehearsal, revenue scenarios, and the customer journey.

- **Billing rehearsal:** a 0–90 day preview calculated using the same billing rules as invoice generation. Surfaces overdue accounts and ineligible discounts, with expected billing and amounts requiring review.
- **Customer journey:** a subscription lifecycle and event timeline from draft through quotation, confirmation, activation, pause, renewal, and closure.
- **Considered visual design:** original CSS/SVG brand artwork, animated orbital illustration, chart transitions, responsive navigation, keyboard search, focus trapping, and reduced-motion support.
- **Billing correctness:** integer minor units, rounded tax calculation, atomic transactions, a unique subscription-period constraint, idempotent payment references, partial payments, and calendar anchors that survive February.

## Included workflows

Customer signup and sign-in; hashed passwords and expiring HttpOnly sessions; single-use password resets; admin-created staff; products with costs, notes and a priced variant; daily, weekly, monthly and yearly plans; minimum quantities and availability dates; quotation templates and validity; multiple product lines; fixed and percentage discounts with dates, product scope, thresholds and usage caps; invoice drafts, confirmation, cancellation, printing and local email queuing; payment records; MRR, revenue, outstanding and overdue reporting; report date/plan filters; CSV exports.

The first subscription line takes the selected plan's per-seat price, plus the optional product variant surcharge. Additional product lines use their catalog sales price on that plan's billing interval. Taxes apply to the discounted total. Invoice lines and totals are stored at billing time.

## Deployed recurring billing

GitHub Actions calls the protected billing endpoint every six hours (`17 */6 * * *`, UTC). A manual workflow dispatch is also available. The randomly generated `CADENCE_BILLING_TOKEN` is stored in GitHub Secrets and Render environment settings, never in source. Scheduling creates drafts only and never collects money. GitHub scheduled runs may be delayed; the idempotent billing engine catches up on the next run.

## Local recurring billing worker

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

## Deployment

The frontend and API share one HTTPS origin on Render Singapore; PostgreSQL 17 uses Render's internal network. `/api/health` verifies database access. The database rejects external internet connections. Environment settings are managed in Render (not a Blueprint): `DATABASE_URL`, `CADENCE_PUBLIC_DEMO=1`, `CADENCE_SECURE_COOKIE=1`, `CADENCE_BILLING_TOKEN`, and `PYTHON_VERSION=3.12.8`.

GitHub Actions runs integration checks for pushes and pull requests. Render automatically deploys the default branch `codex/cadence-build`. Work on feature branches and use pull requests for team collaboration.

## Public demo boundaries

The public instance is a shared hackathon demo with synthetic records. Signup, password reset and staff creation are disabled there; use the three demo roles. The full account workflows remain available locally. Passwords are hashed, cookies are HttpOnly/SameSite/Secure over HTTPS, auth requests are rate limited, cross-origin writes are rejected, and API responses are not cached.

Email is queued in an admin-visible demo outbox; no actual emails are sent. Payments record receipts; no actual card or UPI collection is connected. Do not enter real customer data. Live provider integration is separate from demo billing.

The free Render web service sleeps after inactivity and can take about a minute to wake. Its free database expires on **30 November 2026** unless upgraded. Production use needs paid always-on hosting, backups, stronger auth/tenant isolation, pagination, a connection pool, and real provider integrations. The core billing calculations are tested, but the app is not an audited financial system.

Renewal currently starts a one-year term. One billing pass catches up at most 400 historical periods per subscription; subsequent passes continue at the saved cursor. Taxes are snapshotted per subscription, and invoice lines/totals are snapshotted at billing. Catalog creation is supported; editing live commercial terms and proration remain follow-ups. Google Fonts load online with system fallbacks.

## Live smoke test

Against the synthetic demo only:

```powershell
py verify_live.py https://cadence-odoo.onrender.com
```

This creates one closed verification subscription and a paid synthetic invoice, and checks PostgreSQL, lifecycle transitions, multiple lines, duplicate billing/payment prevention, and customer isolation.
