import random
import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from merchants.models import LedgerEntry
from .models import Payout

logger = logging.getLogger(__name__)


def _return_funds_to_merchant(payout, reason):
    """
    Credit the merchant's ledger to return held funds.
    Must be called inside an atomic transaction alongside the status change.
    """
    LedgerEntry.objects.create(
        merchant=payout.merchant,
        entry_type=LedgerEntry.CREDIT,
        amount_paise=payout.amount_paise,
        description=f'Payout #{payout.id} failed — funds returned: {reason}',
        payout=payout,
    )


@shared_task(bind=True, max_retries=3)
def process_payout(self, payout_id: str):
    """
    Picks up a payout and simulates bank settlement.
    - 70% success
    - 20% failure (funds returned atomically)
    - 10% hang (will be picked up by the reaper task)
    """
    logger.info(f"Processing payout {payout_id}, attempt {self.request.retries + 1}")

    try:
        with transaction.atomic():
            # Lock the payout row so no other worker touches it simultaneously
            payout = Payout.objects.select_for_update().get(pk=payout_id)

            # Guard: only process pending payouts
            if payout.status != Payout.PENDING:
                logger.warning(f"Payout {payout_id} is {payout.status}, skipping")
                return

            # Validate state transition
            if not payout.can_transition_to(Payout.PROCESSING):
                logger.error(f"Illegal transition: {payout.status} → processing for {payout_id}")
                return

            payout.status = Payout.PROCESSING
            payout.processing_started_at = timezone.now()
            payout.attempt_count += 1
            payout.save(update_fields=['status', 'processing_started_at', 'attempt_count', 'updated_at'])

    except Payout.DoesNotExist:
        logger.error(f"Payout {payout_id} not found")
        return

    # --- Simulate bank response (outside the lock — bank call is slow) ---
    outcome = random.random()

    if outcome < 0.70:
        # 70% success
        _mark_completed(payout_id)
    elif outcome < 0.90:
        # 20% failure
        _mark_failed(payout_id, reason="Bank rejected the transfer")
    else:
        # 10% hang — do nothing, the reaper task will handle it after 30s
        logger.info(f"Payout {payout_id} is hanging (simulated)")


def _mark_completed(payout_id: str):
    """Transition payout to completed. Funds stay debited (payout is final)."""
    with transaction.atomic():
        payout = Payout.objects.select_for_update().get(pk=payout_id)

        if not payout.can_transition_to(Payout.COMPLETED):
            logger.error(f"Illegal transition to completed for payout {payout_id} in state {payout.status}")
            return

        payout.status = Payout.COMPLETED
        payout.save(update_fields=['status', 'updated_at'])
        logger.info(f"Payout {payout_id} completed successfully")


def _mark_failed(payout_id: str, reason: str):
    """
    Transition payout to failed AND return funds to merchant.
    Both happen in the same atomic transaction — if either fails, both roll back.
    """
    with transaction.atomic():
        payout = Payout.objects.select_for_update().get(pk=payout_id)

        if not payout.can_transition_to(Payout.FAILED):
            logger.error(f"Illegal transition to failed for payout {payout_id} in state {payout.status}")
            return

        payout.status = Payout.FAILED
        payout.failure_reason = reason
        payout.save(update_fields=['status', 'failure_reason', 'updated_at'])

        # Return funds in the same transaction. If the credit creation fails,
        # the status change rolls back too. You never get a failed payout without the refund.
        _return_funds_to_merchant(payout, reason)
        logger.info(f"Payout {payout_id} failed, funds returned")


@shared_task
def reap_stuck_payouts():
    """
    Periodic task. Finds payouts stuck in processing > 30 seconds and retries or fails them.
    Run every 15 seconds via Celery beat.
    """
    from django.conf import settings
    timeout_seconds = getattr(settings, 'PAYOUT_PROCESSING_TIMEOUT_SECONDS', 30)
    max_attempts = getattr(settings, 'PAYOUT_MAX_ATTEMPTS', 3)

    cutoff = timezone.now() - timedelta(seconds=timeout_seconds)

    stuck_payouts = Payout.objects.filter(
        status=Payout.PROCESSING,
        processing_started_at__lt=cutoff,
    )

    for payout in stuck_payouts:
        logger.warning(f"Payout {payout.id} stuck in processing since {payout.processing_started_at}")

        if payout.attempt_count < max_attempts:
            # Reset to pending and re-enqueue with exponential backoff
            with transaction.atomic():
                p = Payout.objects.select_for_update().get(pk=payout.id)
                if p.status == Payout.PROCESSING:  # Double-check after lock
                    p.status = Payout.PENDING
                    p.save(update_fields=['status', 'updated_at'])

            countdown = (2 ** payout.attempt_count) * 5  # 5s, 10s, 20s
            process_payout.apply_async(args=[str(payout.id)], countdown=countdown)
            logger.info(f"Re-enqueued payout {payout.id} with {countdown}s backoff")
        else:
            # Max attempts reached — fail the payout and return funds
            _mark_failed(str(payout.id), reason=f"Timed out after {max_attempts} attempts")