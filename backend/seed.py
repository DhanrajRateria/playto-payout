"""
Run with: python seed.py (from backend/ with DJANGO_SETTINGS_MODULE set)
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from merchants.models import Merchant, LedgerEntry
from payouts.models import BankAccount, Payout, IdempotencyKey

# Clear in correct FK order: children before parents
print("Clearing old seed data...")
IdempotencyKey.objects.all().delete()   # references Payout + Merchant
LedgerEntry.objects.all().delete()      # references Payout + Merchant
Payout.objects.all().delete()           # references BankAccount + Merchant
BankAccount.objects.all().delete()      # references Merchant
Merchant.objects.all().delete()

# Create merchants
print("Creating merchants...")
m1 = Merchant.objects.create(name="Rohan Verma Design", email="rohan@vermadesign.in")
m2 = Merchant.objects.create(name="Priya Tech Solutions", email="priya@priyatech.io")
m3 = Merchant.objects.create(name="Kiran Content Studio", email="kiran@kirancontent.com")

# Create bank accounts
print("Creating bank accounts...")
b1 = BankAccount.objects.create(
    merchant=m1, account_number="1234567890123456",
    ifsc_code="HDFC0001234", account_holder_name="Rohan Verma"
)
b2 = BankAccount.objects.create(
    merchant=m2, account_number="9876543210987654",
    ifsc_code="ICIC0005678", account_holder_name="Priya Sharma"
)
b3 = BankAccount.objects.create(
    merchant=m3, account_number="1122334455667788",
    ifsc_code="SBIN0009012", account_holder_name="Kiran Rao"
)

# Seed credits (simulated customer payments)
print("Seeding ledger credits...")

# Rohan: 3 payments totalling ₹15,000
LedgerEntry.objects.create(merchant=m1, entry_type='credit', amount_paise=500000,
    description="Customer payment from Acme Corp (Invoice #1001)")
LedgerEntry.objects.create(merchant=m1, entry_type='credit', amount_paise=750000,
    description="Customer payment from Globex Inc (Invoice #1002)")
LedgerEntry.objects.create(merchant=m1, entry_type='credit', amount_paise=250000,
    description="Customer payment from Wayne Enterprises (Invoice #1003)")

# Priya: 2 payments totalling ₹25,000
LedgerEntry.objects.create(merchant=m2, entry_type='credit', amount_paise=1500000,
    description="Customer payment from Stark Industries (Invoice #2001)")
LedgerEntry.objects.create(merchant=m2, entry_type='credit', amount_paise=1000000,
    description="Customer payment from Pied Piper (Invoice #2002)")

# Kiran: 4 payments totalling ₹8,000
LedgerEntry.objects.create(merchant=m3, entry_type='credit', amount_paise=200000,
    description="Customer payment from Dunder Mifflin (Invoice #3001)")
LedgerEntry.objects.create(merchant=m3, entry_type='credit', amount_paise=200000,
    description="Customer payment from Initech (Invoice #3002)")
LedgerEntry.objects.create(merchant=m3, entry_type='credit', amount_paise=200000,
    description="Customer payment from Prestige Worldwide (Invoice #3003)")
LedgerEntry.objects.create(merchant=m3, entry_type='credit', amount_paise=200000,
    description="Customer payment from Bluth Company (Invoice #3004)")

print(f"""
Seed complete.

Merchant ID {m1.id}: {m1.name} — Balance: ₹15,000 | Bank: {b1.id}
Merchant ID {m2.id}: {m2.name} — Balance: ₹25,000 | Bank: {b2.id}
Merchant ID {m3.id}: {m3.name} — Balance: ₹8,000  | Bank: {b3.id}
""")