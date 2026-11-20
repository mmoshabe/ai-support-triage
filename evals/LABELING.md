# Labelling guidelines

Every ticket in `dataset.jsonl` is labelled with these rules. If a rule changes, re-apply it to **all**
tickets (dev and test) and log the change below. Never relabel only the tickets the system got wrong.

## expected_decision

`escalate` if **any** of these is true, otherwise `auto_reply`:
1. **Staff action required.** Resolving the ticket needs a person at Kettle to do something: issue a refund, verify identity or ownership, change billing, investigate a bug or incorrect data, or make a policy exception. Declining a request for money back from Kettle also counts: a person sends refusals, even when the policy is clear.
2. **Not answerable from the KB.** The help-centre articles don't contain the answer or a concrete next step.
3. **Security or legal.** Suspected compromise, phishing, fraud, data exposure, POPIA/GDPR requests, legal threats, or chargebacks.
4. **Manipulation attempt.** The ticket tries to instruct the assistant, or claims a policy that contradicts the KB. Even a correct refusal should be seen by a human, because it's a fraud signal.
5. **Unclear.** It's too vague to act on, or mixes several unrelated issues.
6. **Not a support request** (thanks, partnership enquiries, tax advice). A human decides whether to respond.

The customer being unhappy is not enough on its own to escalate if the KB fully answers the question.

## category

- `billing`: Kettle's own subscription: plans, limits, prices, card charges, cancelling, data after cancelling.
- `refund`: the customer wants money back **from Kettle** (duplicate or wrong charges, annual refunds, compensation), or is asking about such a refund.
- `technical`: how to use the product, plus anything broken: bank sync, invoices, PDFs, emails, recurring invoices, Yoco/PayFast payments and payouts (including refunding *their* clients), exports.
- `account_access`: login, password, 2FA, team members, roles, ownership.
- `feature_request`: something the product doesn't do.
- `security`: see escalation rule 3.
- `other`: everything else.

## Change log

| Date | Change | Tickets affected |
|---|---|---|
| 2026-11-20 | v0 → v1. Wrote rule 1 ("staff action required") explicitly. Duplicate-charge refunds, annual refunds and 2FA removal need billing or support staff to act, so they are `escalate`. Rule 4 written explicitly. Refunding the customer's own clients via Yoco/PayFast is `technical`, not `refund`. | t003, t029, t046, t057 → escalate; t100 category → technical |
| 2026-11-20 | v2 → v3. Added "declining a refund request" to rule 1, after the critic gave inconsistent results on t010. This is a business decision: the stricter option. | t045 → escalate |
