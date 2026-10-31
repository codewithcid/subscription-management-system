# Cadence verification — 31 October 2026

Story: an administrator creates a subscription in the browser, approves it, previews and generates billing, confirms an invoice, and records payment; the database and dashboard reflect the result, while the customer portal exposes only the signed-in customer's data.

| Boundary | Result | Evidence |
| --- | --- | --- |
| Browser renders | Passed | Desktop 1440 × 1000 and phone 390 × 844 inspected; animated dashboard, tables, cards, dialogs, and navigation render. |
| Browser → API | Passed | Created SUB-0009, transitioned Draft → Quotation → Confirmed → Active through UI. |
| API → database | Passed | Billing generated four due draft invoices; atomic/idempotent billing also covered by concurrent integration test. |
| Database → response | Passed | INV-0034 confirmed and payment recorded through UI; subsequent invoice response showed Paid, zero balance. |
| Response → UI | Passed | Updated metrics, lifecycle timeline, invoice balance, and Paid status visible in browser. |
| Role boundaries | Passed | Customer UI omits admin controls; integration tests reject unauthorized actions and another customer's subscription. |
| Responsive layout | Passed | Phone viewport width 390, document scroll width 375; tables scroll inside their containers. |
| Browser errors | Passed | No JavaScript error logs during tested flow. |
| Search and reporting | Passed | Subscription search preserves dashboard chart, report period filter works, additional product-line controls render. |

Eight automated integration tests pass. Coverage includes duplicate concurrent billing, duplicate payment references, exact discount/tax totals, partial and final payment, month-end anchors, quote expiry, multiple product lines, product-scoped discounts, password-reset token reuse/session revocation, and permissions.

One synthetic API benchmark used a temporary database with 5,008 subscriptions and 5,030 invoices. `/api/workspace` returned HTTP 200 in 0.386 seconds with a 4.17 MB response. This measures local API execution only; it does not establish a production or browser-rendering SLA. Pagination remains a production follow-up.

Email is a local outbox and payment entry is a bookkeeping action. Real email delivery, payment gateway collection, hosting, production authentication hardening, and production load testing are not verified or configured.

All changes are local. No source code has been pushed to GitHub.
