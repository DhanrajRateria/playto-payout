from django.shortcuts import render

import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from merchants.models import Merchant, LedgerEntry
from .models import Payout, BankAccount, IdempotencyKey
from .tasks import process_payout


def _serialize_payout(payout):
    """Consistent payout serialization used for both first and cached responses."""
    return {
        'id': str(payout.id),
        'merchant_id': payout.merchant_id,
        'bank_account_id': payout.bank_account_id,
        'amount_paise': payout.amount_paise,
        'status': payout.status,
        'attempt_count': payout.attempt_count,
        'failure_reason': payout.failure_reason,
        'created_at': payout.created_at.isoformat(),
        'updated_at': payout.updated_at.isoformat(),
    }


class PayoutCreateView(APIView):
    """
    POST /api/v1/payouts/
    Header: Idempotency-Key: <uuid>
    Body: { "merchant_id": 1, "amount_paise": 10000, "bank_account_id": 1 }
    """

    def post(self, request):
        # --- 1. Validate idempotency key ---
        idempotency_key = request.headers.get('Idempotency-Key', '').strip()
        if not idempotency_key:
            return Response(
                {'error': 'Idempotency-Key header is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate it's a UUID
        try:
            uuid.UUID(idempotency_key)
        except ValueError:
            return Response(
                {'error': 'Idempotency-Key must be a valid UUID'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # --- 2. Validate request body ---
        merchant_id = request.data.get('merchant_id')
        amount_paise = request.data.get('amount_paise')
        bank_account_id = request.data.get('bank_account_id')

        if not all([merchant_id, amount_paise, bank_account_id]):
            return Response(
                {'error': 'merchant_id, amount_paise, and bank_account_id are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not isinstance(amount_paise, int) or amount_paise <= 0:
            return Response(
                {'error': 'amount_paise must be a positive integer'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            merchant = Merchant.objects.get(pk=merchant_id)
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            bank_account = BankAccount.objects.get(pk=bank_account_id, merchant=merchant, is_active=True)
        except BankAccount.DoesNotExist:
            return Response({'error': 'Bank account not found'}, status=status.HTTP_404_NOT_FOUND)

        # --- 3. Idempotency check (BEFORE acquiring balance lock) ---
        # Check if we have seen this key before for this merchant.
        # We do this outside the main transaction first for a fast path.
        existing_key = IdempotencyKey.objects.filter(
            key=idempotency_key,
            merchant=merchant
        ).select_related('payout').first()

        if existing_key:
            if existing_key.is_expired():
                # Expired keys are treated as new requests
                existing_key.delete()
            else:
                # Return the cached response exactly
                return Response(
                    existing_key.response_body,
                    status=existing_key.response_status
                )

        # --- 4. Acquire balance lock + create payout atomically ---
        # This is the critical section. Everything inside this transaction
        # is serialized at the database level.
        try:
            with transaction.atomic():
                # SELECT FOR UPDATE locks the merchant's ledger entries
                # so no other concurrent transaction can read+modify them
                # until this transaction commits or rolls back.
                #
                # We compute available balance inside the lock so the
                # check-then-deduct is atomic.
                aggregates = LedgerEntry.objects.select_for_update().filter(
                    merchant=merchant
                ).aggregate(
                    total_credits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.CREDIT), default=0),
                    total_debits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.DEBIT), default=0),
                )

                total_credits = aggregates['total_credits']
                total_debits = aggregates['total_debits']

                held_paise = Payout.objects.filter(
                    merchant=merchant,
                    status__in=[Payout.PENDING, Payout.PROCESSING]
                ).aggregate(total=Sum('amount_paise', default=0))['total']

                available_balance = total_credits - total_debits - held_paise

                if available_balance < amount_paise:
                    response_data = {
                        'error': 'Insufficient balance',
                        'available_balance_paise': available_balance,
                        'requested_paise': amount_paise,
                    }
                    response_status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

                    # Cache this failure response too — idempotency applies to errors
                    IdempotencyKey.objects.get_or_create(
                        key=idempotency_key,
                        merchant=merchant,
                        defaults={
                            'response_body': response_data,
                            'response_status': response_status_code,
                            'expires_at': timezone.now() + timedelta(hours=24),
                        }
                    )
                    return Response(response_data, status=response_status_code)

                # --- 5. Create the payout record ---
                payout = Payout.objects.create(
                    merchant=merchant,
                    bank_account=bank_account,
                    amount_paise=amount_paise,
                    status=Payout.PENDING,
                )

                # --- 6. Debit the ledger immediately (funds are "held") ---
                # This is the debit that holds the funds. If the payout fails later,
                # the processor will create a credit to return the funds.
                LedgerEntry.objects.create(
                    merchant=merchant,
                    entry_type=LedgerEntry.DEBIT,
                    amount_paise=amount_paise,
                    description=f'Payout hold #{payout.id}',
                    payout=payout,
                )

                # --- 7. Store idempotency key with response ---
                response_data = _serialize_payout(payout)
                IdempotencyKey.objects.create(
                    key=idempotency_key,
                    merchant=merchant,
                    payout=payout,
                    response_body=response_data,
                    response_status=status.HTTP_201_CREATED,
                    expires_at=timezone.now() + timedelta(hours=24),
                )

        except Exception as e:
            return Response(
                {'error': 'Failed to create payout', 'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # --- 8. Enqueue background processing (outside transaction) ---
        # We trigger this AFTER the transaction commits so the worker
        # can see the payout record in the database.
        process_payout.delay(str(payout.id))

        return Response(response_data, status=status.HTTP_201_CREATED)


class PayoutDetailView(APIView):
    def get(self, request, payout_id):
        try:
            payout = Payout.objects.get(pk=payout_id)
        except Payout.DoesNotExist:
            return Response({'error': 'Payout not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(_serialize_payout(payout))


class PayoutListView(APIView):
    def get(self, request):
        merchant_id = request.query_params.get('merchant_id')
        qs = Payout.objects.all().order_by('-created_at')
        if merchant_id:
            qs = qs.filter(merchant_id=merchant_id)
        return Response([_serialize_payout(p) for p in qs[:50]])