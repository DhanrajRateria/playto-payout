# Playto Payout Engine

Cross-border payout infrastructure. Merchants accumulate INR balance from international payments and withdraw to their bank account. This service handles the payout lifecycle with strict concurrency, idempotency, and data integrity guarantees.

## Architecture

- **Django + DRF** — REST API
- **PostgreSQL** — ledger storage, `SELECT FOR UPDATE` for concurrency
- **Celery + Redis** — async payout processing, periodic stuck-payout reaper
- **React + Vite + Tailwind** — merchant dashboard with 3s live polling

## Setup (local)

### Prerequisites
- Python 3.10+
- PostgreSQL
- Redis
- Node.js 18+

### Backend

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Create .env file
echo "DATABASE_URL=postgresql://playto:playto123@localhost:5432/playto_payout" > .env
echo "REDIS_URL=redis://localhost:6379/0" >> .env

python manage.py migrate
python seed.py
python manage.py runserver
```

### Celery (two separate terminals)

```bash
celery -A config worker --loglevel=info
celery -A config beat --loglevel=info
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Running Tests

```bash
cd backend
python manage.py test payouts --verbosity=2
```

## API Reference

| Method | Path | Header | Body |
|--------|------|--------|------|
| GET | `/api/v1/merchants/` | — | — |
| GET | `/api/v1/merchants/{id}/balance/` | — | — |
| POST | `/api/v1/payouts/` | `Idempotency-Key: <uuid>` | `{merchant_id, amount_paise, bank_account_id}` |
| GET | `/api/v1/payouts/list/?merchant_id={id}` | — | — |
| GET | `/api/v1/payouts/{uuid}/` | — | — |

## Key Design Decisions

**Ledger over stored balance:** Balance is `SUM(credits) - SUM(debits)` computed at query time. Never a mutable column. Eliminates the two-write sync problem.

**`SELECT FOR UPDATE` over application locks:** Database-level row locking makes check-then-deduct atomic across multiple Gunicorn workers (separate OS processes, no shared memory).

**Idempotency keys stored in DB:** Not in Redis/cache. Persisted so they survive restarts and can be queried transactionally alongside the payout creation.

**Atomic failure refunds:** A failed payout's fund return is in the same `transaction.atomic()` as the status change. If the credit fails, the status stays processing. You never get a failed payout without the money back.