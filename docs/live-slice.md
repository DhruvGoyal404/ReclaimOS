# The live Razorpay test-mode slice

The 200+ record batch is simulated and always will be — [SIMULATION.md](../SIMULATION.md)
explains why. This slice exists to prove the *integration* is real: we
authenticate against Razorpay, create real objects, read real error envelopes,
and verify real webhook signatures.

Everything here runs against **test mode**. `RAZORPAY_KEY_ID` must start with
`rzp_test_`; the credential loader refuses anything else
([SECURITY.md](../SECURITY.md)).

## What is recorded

Every call appends to `data/live/transcript.jsonl` — method, path, status, and a
scrubbed request and response. That file is committed, because "we integrated
with Razorpay" is worth nothing while a transcript of real requests with real ids
is worth something.

```bash
uv sync --extra live
uv run reclaimos live probe        # which endpoints this account can reach
uv run reclaimos live reconcile    # observed envelopes vs our modelled taxonomy
uv run reclaimos live transcript   # summarise what has been recorded
```

## What this account can and cannot do — measured, 2026-09-01

`reclaimos live probe`, recorded in the transcript:

| endpoint | status | |
| --- | ---: | --- |
| `/payments` | 200 | reachable |
| `/orders` | 200 | reachable |
| `/customers` | 200 | reachable |
| `/payment_links` | 200 | reachable |
| `/settlements` | 200 | reachable |
| `/plans` | **401** | **not provisioned** |
| `/subscriptions` | **401** | **not provisioned** |

The same key returns 200 on five endpoints and 401 on two, so this is not an
authentication problem. Note the response shape: `{"error":"Unauthorized"}` — a
bare string, not Razorpay's usual `{"error":{"code":…,"description":…}}` envelope.
That is the shape returned for a product the account is not provisioned for.

**Consequence:** the Subscriptions "Charge this now" success/failure simulation —
the mechanism the whole recovery loop was designed around — is unavailable until
Subscriptions is enabled on the account. This is recorded in
[failure-log.md](failure-log.md) rather than worked around quietly.

### To enable Subscriptions (dashboard, manual)

1. Razorpay Dashboard → make sure you are in **Test Mode** (toggle, top left).
2. **Subscriptions** in the left nav. If it is absent, go to
   **Settings → Configuration** or the **Apps / Products** page and request
   *Subscriptions*.
3. Subscriptions is a gated product on some accounts and may require business
   activation. If it cannot be enabled on a test account, the slice proceeds
   without it — see the fallback below, which is not a workaround but a smaller
   true claim.

## Getting a real failed payment

We need one genuine error envelope to reconcile the taxonomy against. Without
Subscriptions, the route is a payment link paid with a **deliberately failing
test card**.

1. Create the link (already done once; re-run to make a fresh one):

   ```bash
   uv run reclaimos live seed
   ```

2. Open the `short_url` it prints in a browser.
3. Pay with a Razorpay **test card chosen to fail**. Razorpay documents test
   cards for both outcomes; use a failure card, or use a success card and then
   select the *failure* option on the simulated bank page.
4. Capture the envelope:

   ```bash
   uv run reclaimos live reconcile
   ```

The reconciliation compares each observed `(error_code, error_source, error_step,
error_reason)` tuple against `domain/decline_codes.py` and reports any our
taxonomy does not contain, plus any field the classifier reads that the live
envelope did not carry.

**A mismatch here is an asset.** "We modelled X, the API sends Y, we changed Z"
is a better artefact than a taxonomy that happened to be right.

## Webhooks (second half of the slice)

Requires a public URL, so a tunnel. Steps, in order:

1. Start the receiver locally (port 8000).
2. Tunnel it:

   ```bash
   ngrok http 8000
   ```

   Copy the `https://….ngrok-free.app` forwarding URL.
3. Razorpay Dashboard → **Settings → Webhooks → Add New Webhook** (in Test Mode):
   - **Webhook URL**: `https://….ngrok-free.app/webhook/razorpay`
   - **Secret**: choose one, and put the same value in `.env` as
     `RAZORPAY_WEBHOOK_SECRET`
   - **Active Events**: `payment.failed`, `payment.captured`,
     `payment_link.paid`, and the `subscription.*` events if Subscriptions is
     enabled.
4. Trigger a payment as above and watch the delivery arrive.

What we are specifically checking, left open at the end of Phase 2:

- Is **`X-Razorpay-Event-Id` present on every delivery?** If it is, the
  body-digest fallback in `ingest/webhook.py` stops mattering entirely. If it is
  not, the `evt_nodedupe_` path is load-bearing and stays.
- Does the body carry `created_at` and an entity id on every event type? That is
  what makes digest-based deduplication safe (see `derive_event_id`).

## If Subscriptions cannot be enabled

The slice then proves: real authentication, real customer and order creation,
**a real payment link created through the same `SEND_PAYMENT_LINK` action the
policy engine chooses**, real error envelopes, and real signature-verified
webhook delivery.

It would not prove a live recurring charge. We would say exactly that, in
SIMULATION.md and in the pitch, and the simulated batch would remain the primary
result — which it always was.
