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
| `/plans` | **401** | **not provisioned — confirmed KYC-gated** |
| `/subscriptions` | **401** | **not provisioned — confirmed KYC-gated** |

The same key returns 200 on five endpoints and 401 on two, so this is not an
authentication problem. Note the response shape: `{"error":"Unauthorized"}` — a
bare string, not Razorpay's usual `{"error":{"code":…,"description":…}}` envelope.
That is the shape returned for a product the account is not provisioned for.

### Subscriptions is unavailable — confirmed, not worked around

Verified from three dashboard tabs: Plans and Subscriptions both show "Something
went wrong" (the same 401 the API returns), and Settings shows the "Activate
your account" banner. The Subscriptions API is gated behind full account (KYC)
activation, which this test account deliberately has not done.

**Consequence:** the Subscriptions "Charge this now" success/failure simulation —
the mechanism the whole recovery loop was designed around — is unavailable on
this account. This is recorded in [failure-log.md](failure-log.md) and stated
plainly here rather than worked around or faked.

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

## What the live slice actually observed — 2026-09-01

Two real failed payments, both carrying the same envelope:

```
pay_TWUAziepTriXRd   BAD_REQUEST_ERROR · business · payment_initiation · international_transaction_not_allowed
pay_TWU9ypzqAxV8QC   BAD_REQUEST_ERROR · business · payment_initiation · international_transaction_not_allowed
```

That tuple was **not** in our taxonomy. We had modelled Razorpay's documented
*issuer* decline codes; this is a `source=business` **pre-authorisation
rejection** — merchant configuration refusing the instrument before any bank is
consulted, because international cards are disabled on the account.

Added as `DeclineClass.HARD_NOT_PERMITTED`, classified **hard / non-retryable**.
Verified end to end:

```
class      : HARD_NOT_PERMITTED  (is_hard=True, ambiguous=False)
propensity : 0.02  recoverable=False
action     : send_payment_link   rule=agent.contact.hard_decline.hard_not_permitted
```

Never retried, one payment link so the customer can pay another way. Full
reasoning in [failure-log.md](failure-log.md).

### The limit of this observation, stated plainly

Both failures were the *same* rejection, because that is what this account's
configuration produced. **The live slice observed one class across two
envelopes.** It does not validate the issuer-decline classes — those remain
supported by the simulated batch and by the documented API shape. Two envelopes
is two envelopes, and the write-up says so rather than implying broader coverage.

`HARD_NOT_PERMITTED` was added *after* the held-out split was scored, so it
carries generation weight `0.00` on every rail. The simulated population is
byte-identical and the sealed split still hashes to `001d7c1c…` — asserted by a
test, not just checked once.

## Webhooks (second half of the slice)

Requires a public URL, so a tunnel.

### Setup — exact steps

1. **Set the webhook secret in `.env`:**

   Choose any string as a secret. Add it to `.env` (gitignored):

   ```
   RAZORPAY_WEBHOOK_SECRET=your_chosen_secret_here
   ```

2. **Start the receiver:**

   ```bash
   uv run reclaimos live webhook
   ```

   Listens on `http://0.0.0.0:8000`. Webhook path: `POST /webhook/razorpay`.
   Health check: `GET /health`.

3. **Tunnel it:**

   In a second terminal:

   ```bash
   ngrok http 8000
   ```

   Copy the `https://xxxx-xx-xx.ngrok-free.app` forwarding URL.

4. **Configure the Razorpay dashboard:**

   Dashboard → make sure you are in **Test Mode** → **Settings → Webhooks →
   Add New Webhook**:

   - **Webhook URL**: `https://xxxx-xx-xx.ngrok-free.app/webhook/razorpay`
   - **Secret**: the exact same string you put in `.env`
   - **Active Events**: check these:
     - `payment.authorized`
     - `payment.captured`
     - `payment.failed`
     - `payment_link.paid`
     - `payment_link.cancelled`
     - `payment_link.expired`

   (No `subscription.*` events — the API is not provisioned.)

5. **Trigger a delivery:**

   Use the existing payment-link flow:

   ```bash
   uv run reclaimos live seed
   ```

   Open the `short_url` it prints in a browser. Pay with a Razorpay test card
   (success card or failure card — both produce webhook deliveries). Watch the
   receiver log the arrival.

6. **Review the results:**

   Ctrl+C the receiver to print the session summary, or run:

   ```bash
   uv run reclaimos live webhook-report
   ```

### What we are specifically checking

Left open at the end of Phase 2:

- Is **`X-Razorpay-Event-Id` present on every delivery?** If it is, the
  body-digest fallback in `ingest/webhook.py` stops mattering entirely. If it is
  not, the `evt_nodedupe_` path is load-bearing and stays.
- Does the body carry `created_at` and an entity id on every event type? That is
  what makes digest-based deduplication safe (see `derive_event_id`).

### What the live run actually showed (2026-09-01)

One real delivery arrived through the tunnel — a `payment.failed` event from the
international-not-allowed payment — signature-verified through `ingest()`,
returned 200, recorded in `data/live/webhooks.jsonl`:

| metric | value |
| --- | --- |
| deliveries | 1 |
| accepted (signature valid) | 1 |
| duplicates | 0 |
| with `X-Razorpay-Event-Id` | 1 (`TWVhmRLB0jTiDx`) |
| without | 0 |

**Resolution.** The header is present and `ingest()` already prefers it (it only
derives an id when the header is `None`), so the header is now the confirmed
primary dedup key. The `evt_nodedupe_` fallback is **kept, not retired**: n=1 on a
single event type is not evidence the header is present on every event type
Razorpay sends. Preferring the header while keeping the fallback is the honest
reading of one observation. See failure-log entry #6.

## What this live slice proves — and what it does not

The slice proves: real authentication, real customer and order creation, **a real
payment link created through the same `SEND_PAYMENT_LINK` action the policy
engine chooses**, real error envelopes, real taxonomy reconciliation (which
surfaced failure-log entry #5 — a business-source pre-auth rejection we had not
modelled), and real signature-verified webhook delivery through the same
`ingest()` pipeline the eval harness uses.

**It does not prove a live recurring charge.** The Subscriptions API is
KYC-gated and unavailable on this account ([confirmed above](#subscriptions-is-unavailable--confirmed-not-worked-around)).
The recurring-recovery logic — 68.0% recovery, +30.7pp over best baseline, zero
mandate violations — is validated by the sealed simulated batch, which never
depended on live subscriptions. [SIMULATION.md](../SIMULATION.md) draws the
line; [EVAL.md](../EVAL.md) labels every rupee figure as simulated INR.
