from django.db import models


class Merchant(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    # All money in paise (1 INR = 100 paise). BigIntegerField, never float.
    # We do NOT store balance as a column because it would be a derived value
    # that could go out of sync. Balance is always computed from the ledger.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.email})"

    class Meta:
        db_table = 'merchants'


class LedgerEntry(models.Model):
    """
    Every money movement is a ledger entry. The balance is always:
        SUM(amount) WHERE entry_type=CREDIT  -  SUM(amount) WHERE entry_type=DEBIT

    This is the core of the system. We never store balance as a mutable column
    because that creates sync problems. The ledger is append-only and immutable.
    """
    CREDIT = 'credit'
    DEBIT = 'debit'
    ENTRY_TYPE_CHOICES = [
        (CREDIT, 'Credit'),
        (DEBIT, 'Debit'),
    ]

    merchant = models.ForeignKey(
        Merchant,
        on_delete=models.PROTECT,   # Never delete a merchant with ledger entries
        related_name='ledger_entries'
    )
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPE_CHOICES)
    # Amount in paise. Always positive. Direction is determined by entry_type.
    amount_paise = models.BigIntegerField()
    description = models.CharField(max_length=500)
    # Link back to the payout that caused this debit/credit (nullable for simulated credits)
    payout = models.ForeignKey(
        'payouts.Payout',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ledger_entries'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ledger_entries'
        indexes = [
            models.Index(fields=['merchant', 'created_at']),
            models.Index(fields=['merchant', 'entry_type']),
        ]

    def __str__(self):
        return f"{self.merchant.name} | {self.entry_type} | {self.amount_paise} paise"