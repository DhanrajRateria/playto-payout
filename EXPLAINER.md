# EXPLAINER.md

## 1. The Ledger

**Balance calculation query:**

```python
aggregates = LedgerEntry.objects.filter(merchant=merchant).aggregate(
    total_credits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.CREDIT), default=0),
    total_debits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.DEBIT), default=0),
)
balance = aggregates['total_credits'] - aggregates['total_debits']
```

This translates to a single SQL query with conditional aggregation:
```sql
SELECT
  SUM(amount_paise) FILTER (WHERE entry_type = 'credit') AS total_credits,
  SUM(amount_paise) FILTER (WHERE entry_type = 'debit')  AS total_debits
FROM ledger_entries
WHERE merchant_id = %s;
```

**Why credits and debits as separate rows instead of a stored balance column?**

A stored balance column is a derived value that lives in two places simultaneously. Every payout requires: (1) write ledger entry, (2) update balance. If step 2 fails after step 1, the balance is wrong. If you read balance between the two writes in a concurrent transaction, you get a dirty read.

The ledger-as-source-of-truth design eliminates this: the ledger IS the balance. There is no second place to go out of sync. The invariant — sum(credits) - sum(debits) = balance — is always true because balance is computed from the ledger, not maintained alongside it.

## 2. The Lock

**Exact code that prevents overdraw:**

```python
with transaction.atomic():
    aggregates = LedgerEntry.objects.select_for_update().filter(
        merchant=merchant
    ).aggregate(
        total_credits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.CREDIT), default=0),
        total_debits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.DEBIT), default=0),
    )
    # ... check balance, create payout, create debit ledger entry
```

**The database primitive: `SELECT FOR UPDATE`**

`select_for_update()` appends `FOR UPDATE` to the generated SQL. PostgreSQL acquires an exclusive row-level lock on all matched ledger entry rows for this merchant. Any other transaction that tries to read those rows with `FOR UPDATE` will block at the database level until the first transaction commits or rolls back.

This makes the check-then-deduct atomic. Two concurrent requests for the same merchant arrive at the `select_for_update()` call simultaneously. PostgreSQL serializes them — one gets the lock, computes the balance, creates the debit, and commits. Only then does the second request proceed, at which point it sees the updated ledger (with the first debit), computes the correct reduced balance, and rejects if insufficient.

Python-level locks (threading.Lock) do not work here because multiple Gunicorn workers are separate processes with separate memory. Only the database is shared.

## 3. The Idempotency

**How the system recognizes a seen key:**

The `IdempotencyKey` model stores `(key, merchant)` as a unique pair. On every POST to `/api/v1/payouts/`, we query:
```python
existing = IdempotencyKey.objects.filter(key=idempotency_key, merchant=merchant).first()
```
If it exists and is not expired, we return `existing.response_body` with `existing.response_status` directly, without touching the payout engine.

**What happens if the first request is in flight when the second arrives:**

The `unique_together = ('key', 'merchant')` constraint on the `IdempotencyKey` model means the second concurrent request will hit a database `IntegrityError` when it tries to `create()` its own key record. We handle this with `get_or_create()` which is atomic at the DB level — only one insert succeeds. The loser gets the existing record and returns its cached response.

Keys are scoped per merchant. The same UUID used by two different merchants creates two independent records.

Keys expire after 24 hours. Expired keys are deleted on the next request with the same key, allowing the key to be reused.

## 4. The State Machine

**Where failed-to-completed is blocked:**

Every transition site calls `payout.can_transition_to(new_status)` before changing state:

```python
# In Payout model (payouts/models.py):
LEGAL_TRANSITIONS = {
    'pending':    ['processing'],
    'processing': ['completed', 'failed'],
    'completed':  [],        # Terminal — nothing allowed
    'failed':     [],        # Terminal — nothing allowed
}

def can_transition_to(self, new_status):
    return new_status in self.LEGAL_TRANSITIONS.get(self.status, [])
```

```python
# In tasks.py, before every status change:
if not payout.can_transition_to(Payout.COMPLETED):
    logger.error(f"Illegal transition to completed for {payout_id} in state {payout.status}")
    return  # Hard stop — no state change, no exception swallowing
```

`completed` and `failed` map to empty lists. `can_transition_to` returns `False` for any input from these states. This is checked with `select_for_update()` inside a transaction, so a race between two workers trying to complete the same payout is also blocked.

## 5. The AI Audit

**What AI gave me (wrong):**

When I asked for the concurrency handling code, the AI wrote:

```python
# AI-generated — WRONG
with transaction.atomic():
    merchant = Merchant.objects.get(pk=merchant_id)
    balance = merchant.balance  # Stored column being read
    if balance >= amount_paise:
        merchant.balance -= amount_paise  # Python arithmetic
        merchant.save()
        payout = Payout.objects.create(...)
```

**What's wrong with it:**

Three bugs in four lines. First, `merchant.balance` assumes balance is a stored column — we don't have one (and shouldn't). Second, even if we did, reading it and writing it in two separate ORM operations inside a transaction is not safe — another transaction can read the same value between your read and your write (non-repeatable read) unless you explicitly lock. Third, `merchant.save()` updates all columns by default, which can overwrite concurrent writes to unrelated columns.

**What I replaced it with:**

```python
with transaction.atomic():
    # Lock the ledger rows — not the merchant row
    aggregates = LedgerEntry.objects.select_for_update().filter(
        merchant=merchant
    ).aggregate(
        total_credits=Sum('amount_paise', filter=Q(entry_type='credit'), default=0),
        total_debits=Sum('amount_paise', filter=Q(entry_type='debit'), default=0),
    )
    available = aggregates['total_credits'] - aggregates['total_debits'] - held
    if available < amount_paise:
        # reject
    # Create payout and ledger debit in same transaction
```

The lock is on the ledger rows (the actual data), not the merchant row. Balance is computed by the database, not by Python. The check and the debit are in the same atomic block so there is no window between them.