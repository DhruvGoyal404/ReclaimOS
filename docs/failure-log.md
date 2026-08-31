# Failure log

Written as things break, not reconstructed afterwards. Newest last.

Format: what broke · how it surfaced · what it cost · what fixed it · what stops it
recurring.

---

## 2026-08-31 — `zoneinfo` has no timezone data on Windows

**What broke.** `ZoneInfo("Asia/Kolkata")` raises `ZoneInfoNotFoundError` on a clean
Windows install. Windows ships no IANA timezone database, and CPython's `zoneinfo`
falls back to the system one.

**How it surfaced.** Anticipated while writing `domain/models.py` rather than at
runtime — the whole domain is IST-aware by design, so this would have broken every
import on the primary dev machine.

**Cost.** Minutes, because it was caught before it was written.

**Fix.** Added `tzdata` as an explicit runtime dependency.

**Prevention.** CI runs on both `ubuntu-latest` and `windows-latest`, so a
platform-specific import failure cannot pass unnoticed again.

---

## 2026-08-31 — decline-code tuples are modelled, not verified

**What is wrong.** `domain/decline_codes.py` uses Razorpay-shaped error envelopes
(`code` / `source` / `step` / `reason`) with plausible values, written from the
shape of the API rather than from observed webhook payloads.

**Why it is logged now.** This is a known gap, not a discovered bug, and logging it
before it bites is the point of this file. If the live test-mode slice returns
different reason strings, the classifier's mapping table is wrong and every
classification metric shifts.

**Planned fix.** When the live slice lands (Phase 2), capture real failed-payment
webhooks, diff the observed tuples against `DECLINE_CODES`, and reconcile. Record
the diff here.

**Interim mitigation.** The taxonomy is a single shared table used by both the
generator and the classifier, so a correction is a one-file change, and
`AMBIGUOUS_TUPLES` is derived rather than hand-maintained.

---

## 2026-08-31 — `ruff format` disagreed with hand-laid-out test fixtures

**What broke.** `ruff format --check` failed CI-equivalent checks on two test files
where dict literals had been hand-compacted for readability.

**Cost.** Under a minute.

**Fix.** Let the formatter own formatting; ran `ruff format .` and stopped
hand-aligning.

**Prevention.** `format --check` is in `tasks.ps1 check` and in CI, so the
disagreement surfaces locally before it reaches a commit.

---

## 2026-08-31 — the rupee sign crashed the CLI on Windows

**What broke.** `reclaimos eval` died with
`UnicodeEncodeError: 'charmap' codec can't encode character '\u20b9'` the first
time it printed a money column. Windows consoles default to a legacy code page
(cp1252 on this machine) which has no INR sign. The `·` separators in the `gen`
output had already been rendering as `?` for the same reason — a visible symptom
nobody had chased.

**How it surfaced.** Running the first full evaluation end to end. Not caught by
the unit tests, because pytest captures stdout through a UTF-8 pipe and never
touches the console encoding.

**Cost.** About ten minutes, most of it deciding on the right fix rather than
finding the fault.

**The tempting wrong fix.** Print `INR` instead of `₹`. That hides the problem
rather than solving it: EVAL.md carries the symbol, and so will every
customer-facing dunning message we generate in Phase 3. The encoding has to work.

**Fix.** `_force_utf8_stdio()` in `cli.py` reconfigures stdout and stderr to
UTF-8 before anything prints, with `errors="replace"` as a second line of defence
so a console that still cannot render a glyph shows a placeholder instead of
killing the run.

**Prevention.** Two regression tests: one asserts stdio ends up UTF-8, and the
end-to-end CLI test asserts `₹` survives all the way into the rendered EVAL.md.

---

## 2026-08-31 — the ceiling was recomputed once per policy

**What was wrong.** The test suite took 172 seconds, which is too slow to run on
every save. Profiling with `pytest --durations` put the time in the end-to-end
evaluation tests.

**Cause.** `run_record` computed the oracle ceiling for its record inline. The
ceiling depends only on the record and the sealed truth — never on the policy
under test — so with five policies it was being computed five times per record,
each time evaluating a 3-action × 7-delay grid across 4 slots.

**Fix.** `compute_oracles()` builds the ceiling for a split once; `evaluate()`
passes it to every policy. The parameter is optional, so a single-record test can
still call `run_record` without ceremony.

**Result.** Suite runtime dropped from 172s to under 20s, and the reported metrics
are byte-identical before and after — which is the check that mattered.

**Note.** Not a correctness bug, but worth logging: a slow suite is a suite that
stops being run, and that *becomes* a correctness problem later.

---

## 2026-08-31 — the Dockerfile shipped unverified

**What is wrong.** `docker build` could not be run on the dev machine: Docker
Desktop was installed but its engine was not running, so the command failed to
connect. The Dockerfile was therefore written but never executed.

**Why it is logged rather than quietly left.** An unverified build file in a repo
that advertises "clone and it runs" is exactly the kind of thing a judge finds by
trying it. Saying "it builds" without having built it would be the actual failure.

**Fix.** Added an `image` job to CI that builds the container on
`ubuntu-latest` and runs two commands inside it. The claim is now verified by
machine, on every push, rather than by assertion.

**Still open.** Until that job runs green on a push, the Dockerfile should be
treated as unproven. Update this entry with the result.

---

## 2026-08-31 — the held-out seal was decorative

**What was wrong.** EVAL.md recorded a SHA-256 of the test split, which looked
like a held-out discipline and was not one. `reclaimos eval` **defaulted** to
`--split test`, so every development run scored the held-out data. Between manual
runs and three `evaluate(split="test")` call sites in the suite, the test split
was read dozens of times while the harness was being built.

**How it surfaced.** A direct challenge: *"were the 75 test records ever loaded,
even read-only, during baseline development?"* The answer was yes, repeatedly.
Nothing in the code had made that visible.

**What limits the damage.** No parameter was fitted to a held-out result. The
baselines have no free parameters — `+24h` and the 3x24h ladder are a-priori
platform defaults, not tuned values — and the world constants were authored
before the first evaluation ran and never revised afterwards.

**What does not limit it.** One test asserted
`ceiling.recovery_rate - best.recovery_rate > 10.0` against the test split, and
that `10.0` was chosen after seeing a 36-point gap. That is held-out knowledge
committed to the repository. Separately, the failure-mix retune consulted
`manifest.family_mix`, which is computed over all 250 records including test — a
distributional read rather than an outcome read, but a read.

**Fix.**

- `eval` and `report` now default to `--split train`. Scoring the held-out split
  requires an explicit `--split test`.
- Every held-out read appends a timestamped, checksummed line to
  `data/runs/held-out-reads.log`, which is committed and exempted from
  `.gitignore`. A checksum proves *which* data was scored; it says nothing about
  how often we looked, and that is the thing that actually corrupts a held-out
  set.
- The headroom assertion moved to the train split.
- EVAL.md carries the disclosure above the numbers, not in a footnote.
- The log is seeded with a backfill entry describing the unrecorded period rather
  than starting from a flattering zero.

**Lesson.** A seal that only records *what* was measured, and never *how many
times*, is a checksum wearing the costume of a protocol.

---

## 2026-08-31 — the "outreach beats retries on net money" finding did not replicate

**What broke.** Re-running the baselines on the development split contradicted the
headline result from Phase 1. On the 75-record test split, `contact_once` netted
more money than `retry_3x_fixed` (₹23,443 vs ₹20,618) despite recovering fewer
records — a striking, quotable, counter-intuitive finding. On the 175-record train
split the ordering reverses decisively:

```
train  n=175   retry_3x ₹68,317   contact ₹37,672   -> ladder wins by ~1.8x
test   n= 75   retry_3x ₹20,618   contact ₹23,443   -> contact wins by ~14%
```

**Cause.** Plan value and decline class are sampled independently, so *which*
policy happens to catch the ₹7,999 and ₹12,999 subscriptions is luck. At n=75
that luck dominates. The warning was already on the page and went unread: the
test-split net intervals overlapped almost completely — retry_3x
[₹11,896, ₹30,445] against contact [₹10,148, ₹42,537].

**How it surfaced.** Only because the held-out fix above forced a re-run on a
different split. Had the seal been honoured from the start, the number would have
been computed on train and the artifact would never have been believed.

**Cost.** No code was wrong. The cost was nearly building a pitch on a result that
sampling noise produced.

**What actually replicates**, on both splits: `contact_once` commits **zero**
hard-decline retries and **zero** mandate violations, while the ladder burns 154
attempts on declines that can never authorise and attempts 5 debits against
consent that had already expired. The defensible claim is not "outreach earns more
money" — it is "the ladder buys its recovery with attempts that cannot work and
with debits it has no mandate for."

**Prevention.** Confidence intervals were already printed and were still read past.
Ranking claims now have to be checked on both splits before they are repeated, and
the interval — not the point estimate — is what gets quoted.

---

## 2026-08-31 — the webhook envelope is modelled, not observed

**What is wrong.** `ingest/webhook.py` parses a Razorpay event envelope written
from the documented shape rather than from captured traffic. Two assumptions are
load-bearing and unverified:

1. That `payload.subscription.entity.id` (falling back to
   `payload.payment.entity.subscription_id`) covers how the subscription id is
   actually carried across `payment.*` and `subscription.*` events.
2. That deriving an event id from the body digest is safe when the
   `X-Razorpay-Event-Id` header is absent — i.e. that two genuinely distinct
   events never share a byte-identical body.

**Why it is logged before it bites.** If assumption 2 is wrong, two real events
would collapse into one row and one failed charge would be silently invisible to
the recovery loop. That is a data-loss bug that no unit test can find, because
our tests generate the bodies they then assert on.

**Planned fix.** When the live test-mode slice lands, capture real
`payment.failed` and `subscription.charged` deliveries, diff the observed
envelopes and headers against the parser, and record the diff here. Same exercise
as the decline-code taxonomy above, and it should be done in the same sitting.

**Interim mitigation.** An explicit `event_id` always wins over the derived one,
so once the header is confirmed present the fallback stops mattering.

---

## 2026-08-31 — SECURITY.md described controls that did not exist yet

**What happened.** SECURITY.md was written in Phase 0 and asserted, in the present
tense, that webhooks are HMAC-verified with a constant-time comparison, that
unverified payloads are recorded as rejected events, and that replay safety comes
from an idempotency layer. None of that code existed until Phase 2.

**Why it matters.** A security page describing intentions in the present tense is
the kind of thing that survives to submission unnoticed, and a judge who greps for
`hmac.compare_digest` and finds nothing has learned something worse about the repo
than "this feature is not built yet".

**Resolution.** As of this commit every claim on that page is implemented and
tested — the constant-time comparison, the reject-and-record path, the
digest-only storage of rejected bodies, and the atomic idempotency claim. Checked
line by line rather than assumed.

**Prevention.** Trust-signal documents get re-read against the code at the end of
each phase, not only when they are written. A claim that is not yet true belongs
in the future tense or in the status table.

---

## 2026-08-31 — the digest-collapse risk, narrowed and made loud

**Follow-up to "the webhook envelope is modelled, not observed" above.** Challenged
on whether the body-digest event id was a deliberate choice or an unexamined
assumption. Looking properly, it was partly the latter, and partly fixable
immediately.

**What was actually true.** The body already carries `created_at` and the entity
ids, and both are inside the digest. So a collision needs two events of the same
type, for the same entity, in the same second — which is a redelivery, not two
distinct facts. The risk was narrower than the original entry implied, and that
entry overstated it.

**What was genuinely wrong.** The code did not *check* that those fields were
present. For an event type whose body carried neither a timestamp nor an entity
id, two distinct events would have collapsed into one row silently — and because
the store is append-only, permanently.

**Fix, shipped now rather than deferred to the live slice.** `derive_event_id`
uses the digest only when the body carries a timestamp *and* an entity id.
Otherwise the id is made unique per delivery and prefixed `evt_nodedupe_`, so the
condition is visible in the store instead of inferred.

**The principle, which generalises:** *fail toward a duplicate, never toward a
collapse.* A duplicate row is visible and reconcilable. Two distinct failed
charges merged into one is invisible, permanent, and costs a customer their
recovery. The asymmetry is not close, so the default should never have been the
other way.

**Still open, and genuinely needs the live envelope:** whether Razorpay's
`X-Razorpay-Event-Id` header is present on every delivery. If it is, the fallback
stops mattering entirely — a test already covers that case.

---

## 2026-08-31 — `ledger verify` existed but nothing ran it

**What was wrong.** The tamper-evidence tests (including the ones that
`DROP TRIGGER` and rewrite a row) were in the pytest suite CI runs, so those were
covered. But `reclaimos ledger verify` — the command the README tells a judge to
run, and the one that exits non-zero on a break — was invoked by nothing except a
human typing it.

**Why that matters.** The guarantee was real and the wiring was not. A
verification nothing invokes is a claim, not a control, and the gap is invisible
because every individual piece looks correct.

**Fix.** Added `reclaimos ledger demo`, which seeds a small real audit trail
(signed webhook deduplicated, forged webhook refused, idempotency replay lost,
three chained ledger entries), and a CI step that runs it and then runs
`ledger verify`. A broken chain now fails the build. The demo doubles as the thing
to run on camera.

**Prevention.** Same class of gap as the SECURITY.md entry: something asserted but
not exercised. Each phase now ends by asking not "does the guarantee hold" but
"what invokes it".

---

## 2026-08-31 — the import-boundary guard fired on its first real test

**What happened.** The first Phase 3 module, `diagnose/propensity.py`, defined a
constant named `ATTEMPT_DECAY`. So does `generator/outcome_model.py`. The
boundary guard written in Phase 2 — before any classifier existed — failed the
build immediately.

**Was it a real problem?** Not a copy: our 0.65 was chosen independently of the
world's 0.62, and the two mean different things (ours discounts a *propensity*,
the world's discounts a *retry success probability*). So the guard was, strictly,
flagging a name collision rather than circularity.

**Fixed anyway, and the guard kept strict.** Renamed ours to
`SPENT_ATTEMPT_DECAY`. Two identically-named constants either side of the
world/policy boundary are exactly how a copy sneaks in later — someone greps
`ATTEMPT_DECAY`, finds two definitions, "unifies" them, and the benchmark closes
on itself with no test failing. Loosening the guard to allow same-name-different-
value would have removed the only mechanism that catches that.

**Why this is worth recording.** The guard was written speculatively in Phase 2
against code that did not exist. It caught something within an hour of that code
being written. That is the argument for writing invariants before the thing they
constrain, rather than after.

---

## 2026-08-31 — the fail-closed path was coercing garbage instead of refusing it

**What broke.** `_parse` built the `Explanation` with `str(data["root_cause"])`.
Python's `str()` never fails, so a model returning `null` produced the literal
string `"None"`, and one returning `{"a": 1}` produced `"{'a': 1}"` — both
accepted as genuine model output, labelled `source="model"`, and destined for an
append-only ledger where they could not be corrected.

**How it surfaced.** The forced-failure table in `tests/test_explainer.py`. Two of
its ten malformed responses came back as `source="model"` instead of the template.
Waiting for a real malformed response would never have found this, because the
failure is silent — the row looks fine, it just says "None".

**Fix.** Check the types, do not coerce them: both fields must be `str` and
non-empty, or the response is unusable and the template runs. Coercion is how a
fail-closed path quietly stops being one.

---

## 2026-08-31 — a self-consistent redactor reported text as clean while leaking it

**What broke.** `redact()` missed `+91 98765 43210`. The pattern required ten
contiguous digits and the number has a space in it. It also missed
`+919876543210`, where a `\b` could not match between the country code and the
number.

**The part worth recording.** `redact()` and `contains_pii()` share one pattern
list. So a pattern that fails to match leaves the secret in the text *and*
reports the text as clean. A test asserting only `not contains_pii(output)` would
have passed on leaked PII — the check and the thing being checked had the same
blind spot.

**How it surfaced.** Only because the test also asserted `secret not in output`.
That redundancy looked like belt-and-braces when written and turned out to be the
entire value of the test.

**Fix.** Replaced the word-boundary anchors with digit lookarounds so separators
and country codes are handled, added the missed forms to the parametrised cases,
and added a test that states explicitly why the literal-substring assertion
exists — so nobody later "simplifies" it to the `contains_pii` check.

**Generalises to:** never validate an output with the same predicate that produced
it. The assertion has to come from outside the mechanism under test.

---

## 2026-08-31 — the agent reported itself as a runaway

**What broke.** `test_the_agent_always_terminates` failed for three decline
classes with `attempt_cap_reached` — the terminal state the harness assigns to a
policy it had to stop. The agent had spent 3 charge attempts and 2 contacts, well
inside its own budget, and then halted voluntarily.

**Cause.** The agent's budget-exhaustion branch returned
`TerminalReason.ATTEMPT_CAP_REACHED`. That reason is not in the harness's
`SELF_HALTED` set, so a perfectly well-behaved stop was being counted as "the
harness had to intervene" — dropping the agent's `self_halt_rate` below 100% and
conflating a policy that chooses to stop with one that has to be stopped.

**Why it matters beyond a label.** `self_halt_rate` is one of the three safety
invariants EVAL.md reports. Had this shipped, the agent's own safety column would
have understated it, and the distinction between "stopped itself" and "was
stopped" — which is the whole point of tracking it — would have been meaningless.

**Fix.** Budget exhaustion is `POLICY_STOPPED`. `ATTEMPT_CAP_REACHED` stays
reserved for the harness's runaway guard, which is what it is for.

---

## 2026-08-31 — the agent's remaining hard-decline retries are the error floor, not a bug

**What looked like a defect.** The agent retried hard declines 22 times across the
development split, and the test asserting zero failed.

**What it actually is.** Every one of those 22 retries was on a record whose
gateway tuple is emitted by soft declines too — the ambiguous
`(BAD_REQUEST_ERROR, payment_failed)` case the taxonomy deliberately preserves.
All 11 affected records classified as `SOFT_INSUFFICIENT_FUNDS`, which is the
correct reading of the evidence available. No classifier reading only the payload
could have done better.

**Resolution.** The assertion was wrong, not the code. It has been replaced with
the property that is actually true and actually worth guaranteeing: *every hard
retry the agent makes is on an unresolvable tuple*. If the agent ever retries a
hard decline whose tuple was unambiguous, that is a real mistake and the test now
catches it — where a blanket `== 0` would have been either permanently red or
quietly deleted.

**Worth saying out loud:** a metric of zero here would have meant the ambiguity
floor had been removed from the generator, which would make every classification
number in EVAL.md meaningless. The non-zero figure is evidence the benchmark is
still honest.

---

## 2026-08-31 — the results table announced the wrong record count

**What broke.** `reclaimos eval --split train` printed "75 records" in its table
title. The train split has 175. The CLI title interpolated `manifest.n_test`
regardless of which split was actually scored.

**Impact.** None on any stored artefact — every JSON and Markdown figure was
computed from the real records. Purely the on-screen title. But it is the table
that goes on camera in the pitch, and a judge who notices a 75 next to a 175-row
result has learned something about how carefully the rest was checked.

**Fix.** The title now reports the row count of the metrics actually being shown.

---

## 2026-08-31 — a circular import the whole test suite could not see

**What broke.** `from reclaimos.policy import AgentConfig` raised
`ImportError: cannot import name 'ReclaimAgent' from partially initialized
module`. The cycle: `policy/__init__` → `policy.agent` → `eval.policy` →
`eval/__init__` → `eval.runner` → `policy.agent`.

**How it surfaced.** By accident. 279 tests were green; I ran a one-line script
to print the agent's parameter list for a status report, and it crashed on the
import. Pytest imports `reclaimos.eval` first for other reasons, which breaks the
cycle before `reclaimos.policy` is ever the entry point — so the suite could not
have caught it no matter how many assertions it had.

**The part that generalises.** A test suite only ever exercises the import orders
its own collection happens to produce. Any cycle that resolves under that order is
invisible to it, however thorough the tests are. This one would have hit the first
person to `import reclaimos.policy` in a notebook or a script.

**Fix.** `LoopState` was only ever a type annotation, so it moved under
`TYPE_CHECKING`. The policy layer now has no runtime dependency on the eval
package, and the dependency runs one way: eval → policy.

**Prevention.** `test_every_module_imports_cleanly_on_its_own` imports each module
in a fresh interpreter, one at a time. Slower than an in-process check and that is
the point — an in-process check would share the same already-imported modules and
reproduce exactly the blind spot being guarded against.

---

## 2026-09-01 — the API the whole loop was designed around returns 401

**What broke.** The first authenticated probe of the live test account:

```
GET /payments      -> 200      GET /customers     -> 200
GET /orders        -> 200      GET /payment_links -> 200
GET /plans         -> 401  {"error":"Unauthorized"}
GET /subscriptions -> 401  {"error":"Unauthorized"}
```

Same key, same request signature, five endpoints fine and two refused.

**Diagnosis.** Not an authentication failure. Two details settle it: the key works
everywhere else on the same call, and the error body is a bare
`{"error":"Unauthorized"}` rather than Razorpay's usual
`{"error":{"code":…,"description":…}}` envelope — the shape returned for a product
the account is not provisioned for. Subscriptions is a gated product.

**Why it matters more than it looks.** ReclaimOS is a *subscription* recovery
agent. The Subscriptions "Charge this now" success/failure simulation is the exact
mechanism cited in CLAUDE.md as the reason this problem was drivable in test mode
at all. The one API the design leaned on is the one the account cannot reach.

**What it does not change.** The 200+ record result was always simulated and
always said so ([SIMULATION.md](../SIMULATION.md)); the live slice was only ever
proof-of-integration. So this narrows the live claim rather than invalidating the
headline number. But it narrows it, and the honest move is to say by how much
instead of quietly redefining what the slice was for.

**How the SDK hid it.** `razorpay.Client().plan.all()` raised `ServerError:` with
an empty message. No status code, no body. Dropping to raw `requests` for the
probe is what turned an unreadable exception into a diagnosis, and is why
`live/client.py` uses `requests` rather than the SDK for reads — the status and
body are the evidence the slice exists to collect, so they must not be swallowed.

**Resolution, in progress.** Asked for Subscriptions to be enabled on the account.
If it can be, the slice proceeds as designed. If it cannot, the slice proves real
authentication, real customer and order creation, a real payment link created
through the same `SEND_PAYMENT_LINK` action the policy engine chooses, real error
envelopes, and real signature-verified webhook delivery — and we state plainly
that it does not prove a live recurring charge. Full detail in
[live-slice.md](live-slice.md).

---

## 2026-09-01 — modelled issuer declines; the live API sent a business-source pre-auth rejection

**What we modelled.** `domain/decline_codes.py` was written in Phase 0 from
Razorpay's documented *issuer* decline codes — insufficient funds, do-not-honor,
card reported stolen, expired instrument. Every entry assumed the refusal came
from a bank looking at a customer's account.

**What the live API actually sent.** Two real test-mode payments, both:

```
BAD_REQUEST_ERROR · business · payment_initiation · international_transaction_not_allowed
```

`source=business`, `step=payment_initiation`. Not an issuer decision at all — a
**pre-authorisation rejection by merchant configuration**, decided before any bank
was consulted. International cards are disabled on the account, so the payment was
refused on the way out.

**Why this is the interesting one.** It is a concrete live instance of the thesis
the whole project argues. A pre-auth rejection *looks* like an ordinary declined
charge — same `BAD_REQUEST_ERROR` code, same failed payment, same webhook. A
retry ladder would spend three attempts on it. But nothing about the customer or
the bank participates in the refusal, so the same card fails identically forever.
It is the most non-retryable thing in the taxonomy, and it superficially resembles
the most retryable.

If it had landed in a soft bucket, the agent would have burned attempts on
something that can never authorise — the exact waste pattern the headline result
is built on avoiding.

**What we changed.** Added `DeclineClass.HARD_NOT_PERMITTED`, classified hard, so
`is_hard` is true and the agent's `wants_charge` gate refuses it before a charge
is ever proposed. Propensity 0.02, below the retry floor. The subscription is
still reachable — but only by asking the customer for a different instrument,
which is the outreach path, and the agent does send exactly one payment link.

**What we were careful not to break.** The class was added *after* the held-out
split was scored. It carries generation weight **0.00 on every rail**, so the
simulated population is byte-identical and the sealed test split still hashes to
`001d7c1c…` — verified, and now asserted by a test that fails if the generator
ever produces different data than the number in EVAL.md describes.

**What this does not prove.** Both live failures were the same
international-not-allowed rejection, because that is what the test account's
configuration produced. The live slice therefore observed **one** class, on two
envelopes. The issuer-decline classes remain validated by the simulated batch and
by the documented API shape — not by this slice. Two envelopes is two envelopes.

## 2026-09-01 — the event-id header is present, but n=1 does not license retiring the fallback

**The open question.** Phase 2 left one residual (see *"the webhook envelope is

modelled, not observed"*): Razorpay''s docs say deliveries carry

`X-Razorpay-Event-Id`, but we had never seen one. `derive_event_id` was built to

fail safe without it — prefer the header when present, and when it is absent fall

back to a body digest, deduplicating only when the body is distinguishing and

otherwise emitting a visible `evt_nodedupe_` id. The question was whether the

header actually arrives, which would make that fallback dead weight.

**What the live slice showed.** One real delivery arrived through the ngrok

tunnel — a `payment.failed` event triggered by the international-not-allowed

payment from entry #5 — signature-verified through `ingest()`, returned 200, and

it **did** carry the header:

deliveries received: 1
with X-Razorpay-Event-Id: 1 (id: TWVhmRLB0jTiDx)
without: 0



So the header is real, and the code already does the right thing with it:

`ingest()` uses the passed-in `event_id` as the dedup key and only derives when it

is `None`. The header wins when present, exactly as the docstring claims. No code

change was needed — this entry is the evidence that the header path is exercised,

not just written.

**The mistake we did not make.** The receiver''s own summary printed an action:

*"CONSISTENT — retire evt_nodedupe_ fallback."* We did not. One delivery of one

event type is evidence that the header **can** be used, not that it is present on

**every** event type Razorpay sends (payment.*, refund.*, settlement.*, and older

deliveries may differ). Deleting a load-bearing defensive path on a single

observation is the "one match proves nothing" trap, one level down from the eval

discipline — and the fallback costs nothing to keep. The honest position is:

**prefer the header (already the case), keep the fallback for the event types we

have not observed.**

**What this does not prove.** n=1, one event type, one account. The header''s

universal presence is not established and we do not claim it. The fallback stays,

and stays tested.

