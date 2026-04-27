from django.shortcuts import render

from django.db.models import Sum, Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Merchant, LedgerEntry
from payouts.models import Payout


class MerchantBalanceView(APIView):
    """
    Returns available balance, held balance, and recent ledger entries.
    All arithmetic is done at the database level with SUM aggregations.
    """

    def get(self, request, merchant_id):
        try:
            merchant = Merchant.objects.get(pk=merchant_id)
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=status.HTTP_404_NOT_FOUND)

        # --- BALANCE CALCULATION ---
        # This is a single SQL query with conditional aggregation.
        # Django translates this to:
        #   SELECT
        #     SUM(CASE WHEN entry_type='credit' THEN amount_paise ELSE 0 END) as total_credits,
        #     SUM(CASE WHEN entry_type='debit' THEN amount_paise ELSE 0 END) as total_debits
        #   FROM ledger_entries WHERE merchant_id = %s
        #
        # We never fetch rows and add them up in Python. That would be wrong
        # because it bypasses DB-level consistency guarantees.
        aggregates = LedgerEntry.objects.filter(merchant=merchant).aggregate(
            total_credits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.CREDIT), default=0),
            total_debits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.DEBIT), default=0),
        )

        total_credits = aggregates['total_credits']
        total_debits = aggregates['total_debits']

        # Held = sum of all pending payouts (funds deducted from ledger but not yet settled)
        held_paise = Payout.objects.filter(
            merchant=merchant,
            status__in=[Payout.PENDING, Payout.PROCESSING]
        ).aggregate(total=Sum('amount_paise', default=0))['total']

        # Gross balance (includes held)
        gross_balance = total_credits - total_debits
        # Available = what merchant can actually withdraw
        available_balance = gross_balance - held_paise

        # Recent ledger entries (last 20)
        recent_entries = LedgerEntry.objects.filter(
            merchant=merchant
        ).select_related('payout').order_by('-created_at')[:20]

        entries_data = [
            {
                'id': e.id,
                'type': e.entry_type,
                'amount_paise': e.amount_paise,
                'description': e.description,
                'payout_id': str(e.payout.id) if e.payout else None,
                'created_at': e.created_at.isoformat(),
            }
            for e in recent_entries
        ]

        return Response({
            'merchant_id': merchant.id,
            'merchant_name': merchant.name,
            'available_balance_paise': available_balance,
            'held_balance_paise': held_paise,
            'gross_balance_paise': gross_balance,
            'total_credits_paise': total_credits,
            'total_debits_paise': total_debits,
            'recent_entries': entries_data,
        })


class MerchantListView(APIView):
    def get(self, request):
        merchants = Merchant.objects.all().order_by('created_at')
        return Response([
            {'id': m.id, 'name': m.name, 'email': m.email}
            for m in merchants
        ])
    
class BalanceInvariantCheckView(APIView):
    """
    Public debug endpoint. Proves ledger invariant holds across all merchants.
    SUM(credits) - SUM(debits) >= 0 must always be true.
    """
    def get(self, request):
        from django.db.models import Sum, Q
        from payouts.models import Payout

        results = []
        all_valid = True

        for merchant in Merchant.objects.all():
            agg = LedgerEntry.objects.filter(merchant=merchant).aggregate(
                credits=Sum('amount_paise', filter=Q(entry_type='credit'), default=0),
                debits=Sum('amount_paise', filter=Q(entry_type='debit'), default=0),
            )
            net = agg['credits'] - agg['debits']

            held = Payout.objects.filter(
                merchant=merchant,
                status__in=['pending', 'processing']
            ).aggregate(total=Sum('amount_paise', default=0))['total']

            available = net - held
            invariant_valid = net >= 0

            if not invariant_valid:
                all_valid = False

            results.append({
                'merchant': merchant.name,
                'merchant_id': merchant.id,
                'total_credits_paise': agg['credits'],
                'total_debits_paise': agg['debits'],
                'net_balance_paise': net,
                'held_paise': held,
                'available_paise': available,
                'invariant_valid': invariant_valid,
            })

        return Response({
            'all_invariants_valid': all_valid,
            'merchants': results,
        })