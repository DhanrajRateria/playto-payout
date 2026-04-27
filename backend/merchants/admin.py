from django.contrib import admin
from .models import Merchant, LedgerEntry

@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'email', 'created_at']
    search_fields = ['name', 'email']

@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ['id', 'merchant', 'entry_type', 'amount_paise', 'description', 'created_at']
    list_filter = ['entry_type', 'merchant']
    search_fields = ['description']
    readonly_fields = ['created_at']