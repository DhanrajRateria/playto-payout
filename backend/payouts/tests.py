import uuid
import threading
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from merchants.models import Merchant, LedgerEntry
from payouts.models import Payout, BankAccount, IdempotencyKey


def create_merchant_with_balance(name, email, balance_paise):
    """Helper to create a merchant with a given credit balance."""
    merchant = Merchant.objects.create(name=name, email=email)
    bank = BankAccount.objects.create(
        merchant=merchant,
        account_number="1234567890123456",
        ifsc_code="HDFC0001234",
        account_holder_name=name
    )
    LedgerEntry.objects.create(
        merchant=merchant,
        entry_type=LedgerEntry.CREDIT,
        amount_paise=balance_paise,
        description="Test seeded credit",
    )
    return merchant, bank


class IdempotencyTest(TestCase):
    """
    Two calls with the same Idempotency-Key must return identical responses
    and create only one payout.
    """

    def setUp(self):
        self.client = APIClient()
        self.merchant, self.bank = create_merchant_with_balance(
            "Idempotency Merchant", "idem@test.com", 1000000  # ₹10,000
        )
        self.idempotency_key = str(uuid.uuid4())

    def test_same_key_returns_same_response_and_single_payout(self):
        payload = {
            'merchant_id': self.merchant.id,
            'amount_paise': 100000,  # ₹1,000
            'bank_account_id': self.bank.id,
        }
        headers = {'HTTP_IDEMPOTENCY_KEY': self.idempotency_key}

        # First request
        resp1 = self.client.post('/api/v1/payouts/', payload, format='json', **headers)
        self.assertEqual(resp1.status_code, 201)

        # Second request — same key
        resp2 = self.client.post('/api/v1/payouts/', payload, format='json', **headers)
        self.assertEqual(resp2.status_code, 201)

        # Responses must be identical
        self.assertEqual(resp1.data['id'], resp2.data['id'])
        self.assertEqual(resp1.data['amount_paise'], resp2.data['amount_paise'])

        # Only one payout must exist
        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(payout_count, 1)

        # Only one idempotency key record
        key_count = IdempotencyKey.objects.filter(
            merchant=self.merchant,
            key=self.idempotency_key
        ).count()
        self.assertEqual(key_count, 1)

    def test_different_keys_create_different_payouts(self):
        payload = {
            'merchant_id': self.merchant.id,
            'amount_paise': 100000,
            'bank_account_id': self.bank.id,
        }
        resp1 = self.client.post(
            '/api/v1/payouts/', payload, format='json',
            **{'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
        )
        resp2 = self.client.post(
            '/api/v1/payouts/', payload, format='json',
            **{'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
        )
        self.assertEqual(resp1.status_code, 201)
        self.assertEqual(resp2.status_code, 201)
        self.assertNotEqual(resp1.data['id'], resp2.data['id'])


class ConcurrencyTest(TransactionTestCase):
    """
    Two simultaneous payout requests for more than available balance.
    Exactly one must succeed, one must be rejected.

    Uses TransactionTestCase (not TestCase) because SELECT FOR UPDATE
    requires real transactions that commit to the DB.
    """

    def setUp(self):
        self.client = APIClient()
        self.merchant, self.bank = create_merchant_with_balance(
            "Concurrency Merchant", "concurrency@test.com", 10000  # ₹100
        )

    def test_concurrent_overdraw_rejected(self):
        results = []
        errors = []

        def make_request():
            # Each thread gets its own client instance
            client = APIClient()
            payload = {
                'merchant_id': self.merchant.id,
                'amount_paise': 6000,  # ₹60 — two of these would exceed ₹100
                'bank_account_id': self.bank.id,
            }
            try:
                resp = client.post(
                    '/api/v1/payouts/', payload, format='json',
                    **{'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
                )
                results.append(resp.status_code)
            except Exception as e:
                errors.append(str(e))

        # Fire two threads simultaneously
        t1 = threading.Thread(target=make_request)
        t2 = threading.Thread(target=make_request)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
        self.assertEqual(len(results), 2)

        # Exactly one success, one failure
        success_count = results.count(201)
        failure_count = results.count(422)
        self.assertEqual(success_count, 1, f"Expected 1 success, got {success_count}. Results: {results}")
        self.assertEqual(failure_count, 1, f"Expected 1 failure, got {failure_count}. Results: {results}")

        # Only one payout created
        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(payout_count, 1)


class StateMachineTest(TestCase):
    """Illegal state transitions must be blocked."""

    def setUp(self):
        self.merchant, self.bank = create_merchant_with_balance(
            "State Merchant", "state@test.com", 1000000
        )

    def test_completed_to_pending_blocked(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account=self.bank,
            amount_paise=100000,
            status=Payout.COMPLETED,
        )
        self.assertFalse(payout.can_transition_to(Payout.PENDING))

    def test_failed_to_completed_blocked(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account=self.bank,
            amount_paise=100000,
            status=Payout.FAILED,
        )
        self.assertFalse(payout.can_transition_to(Payout.COMPLETED))

    def test_pending_to_processing_allowed(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account=self.bank,
            amount_paise=100000,
            status=Payout.PENDING,
        )
        self.assertTrue(payout.can_transition_to(Payout.PROCESSING))

    def test_processing_to_completed_allowed(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account=self.bank,
            amount_paise=100000,
            status=Payout.PROCESSING,
        )
        self.assertTrue(payout.can_transition_to(Payout.COMPLETED))

    def test_balance_invariant_after_failed_payout(self):
        """A failed payout must return funds — balance must equal pre-payout balance."""
        from payouts.tasks import _mark_failed
        from django.db.models import Sum, Q

        initial_credits = LedgerEntry.objects.filter(
            merchant=self.merchant, entry_type=LedgerEntry.CREDIT
        ).aggregate(total=Sum('amount_paise', default=0))['total']

        # Create a payout and debit the ledger (as the view would)
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account=self.bank,
            amount_paise=100000,
            status=Payout.PENDING,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant, entry_type=LedgerEntry.DEBIT,
            amount_paise=100000, description="Test hold", payout=payout
        )
        payout.status = Payout.PROCESSING
        payout.attempt_count = 1
        payout.save()

        # Fail the payout
        _mark_failed(str(payout.id), reason="Test failure")

        # Balance must be restored
        agg = LedgerEntry.objects.filter(merchant=self.merchant).aggregate(
            credits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.CREDIT), default=0),
            debits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.DEBIT), default=0),
        )
        net_balance = agg['credits'] - agg['debits']
        self.assertEqual(net_balance, initial_credits, "Balance must be fully restored after failed payout")