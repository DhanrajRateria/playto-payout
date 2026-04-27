import uuid
from django.db import models
from django.utils import timezone


class IdempotencyKey(models.Model):
    """
    Stores the result of the first call so repeated calls return identical responses.
    Scoped per merchant — same key from two different merchants is two different records.
    """
    key = models.CharField(max_length=255)
    merchant = models.ForeignKey(
        'merchants.Merchant',
        on_delete=models.CASCADE,
        related_name='idempotency_keys'
    )
    # The payout created by the first request
    payout = models.OneToOneField(
        'Payout',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='idempotency_key_record'
    )
    # Full response body of the first request, stored as JSON
    response_body = models.JSONField(null=True, blank=True)
    response_status = models.IntegerField(default=200)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = 'idempotency_keys'
        unique_together = ('key', 'merchant')  # Scoped per merchant
        indexes = [
            models.Index(fields=['expires_at']),  # For cleanup job
        ]

    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.key} | {self.merchant.name}"


class BankAccount(models.Model):
    merchant = models.ForeignKey(
        'merchants.Merchant',
        on_delete=models.CASCADE,
        related_name='bank_accounts'
    )
    account_number = models.CharField(max_length=20)
    ifsc_code = models.CharField(max_length=11)
    account_holder_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bank_accounts'

    def __str__(self):
        return f"{self.account_holder_name} | {self.account_number[-4:].rjust(len(self.account_number), '*')}"


class Payout(models.Model):
    # --- State Machine ---
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (PROCESSING, 'Processing'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
    ]

    # Legal transitions only. Used in the processor and API to validate moves.
    LEGAL_TRANSITIONS = {
        PENDING: [PROCESSING],
        PROCESSING: [COMPLETED, FAILED],
        COMPLETED: [],      # Terminal state — no transitions allowed
        FAILED: [],         # Terminal state — no transitions allowed
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        'merchants.Merchant',
        on_delete=models.PROTECT,
        related_name='payouts'
    )
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.PROTECT,
        related_name='payouts'
    )
    amount_paise = models.BigIntegerField()  # Never float, never Decimal
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    attempt_count = models.IntegerField(default=0)
    # When this payout was picked up for processing
    processing_started_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payouts'
        indexes = [
            models.Index(fields=['merchant', 'status']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['processing_started_at']),
        ]

    def can_transition_to(self, new_status):
        """State machine guard. Call this before every status change."""
        return new_status in self.LEGAL_TRANSITIONS.get(self.status, [])

    def __str__(self):
        return f"Payout {self.id} | {self.merchant.name} | {self.amount_paise} paise | {self.status}"