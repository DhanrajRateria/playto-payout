from django.contrib import admin
from .models import Payout, BankAccount, IdempotencyKey

@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ['id', 'merchant', 'amount_paise', 'status', 'attempt_count', 'created_at']
    list_filter = ['status', 'merchant']
    readonly_fields = ['id', 'created_at', 'updated_at']

@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ['id', 'merchant', 'account_holder_name', 'ifsc_code', 'is_active']

@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ['key', 'merchant', 'response_status', 'created_at', 'expires_at']
    readonly_fields = ['created_at']