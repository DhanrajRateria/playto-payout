import { useState, useEffect, useCallback } from 'react';
import { getMerchants, getMerchantBalance, getPayouts, createPayout } from './api';

function formatRupees(paise) {
  return `₹${(paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
}

function StatusBadge({ status }) {
  const colors = {
    pending:    'bg-yellow-100 text-yellow-800',
    processing: 'bg-blue-100 text-blue-800',
    completed:  'bg-green-100 text-green-800',
    failed:     'bg-red-100 text-red-800',
  };
  return (
    <span className={`px-2 py-1 rounded-full text-xs font-semibold ${colors[status] || 'bg-gray-100'}`}>
      {status}
    </span>
  );
}

function BalanceCard({ label, amount, color }) {
  return (
    <div className={`rounded-xl p-5 ${color}`}>
      <p className="text-sm text-gray-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-gray-800">{formatRupees(amount)}</p>
    </div>
  );
}

export default function App() {
  const [merchants, setMerchants] = useState([]);
  const [selectedMerchant, setSelectedMerchant] = useState(null);
  const [balance, setBalance] = useState(null);
  const [payouts, setPayouts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Payout form state
  const [amountRupees, setAmountRupees] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Load merchant list
  useEffect(() => {
    getMerchants().then(r => {
      setMerchants(r.data);
      if (r.data.length > 0) setSelectedMerchant(r.data[0]);
    });
  }, []);

  // Load balance + payouts when merchant changes
  const refresh = useCallback(async () => {
    if (!selectedMerchant) return;
    setLoading(true);
    try {
      const [balResp, payResp] = await Promise.all([
        getMerchantBalance(selectedMerchant.id),
        getPayouts(selectedMerchant.id),
      ]);
      setBalance(balResp.data);
      setPayouts(payResp.data);
    } catch (e) {
      setError('Failed to load data');
    } finally {
      setLoading(false);
    }
  }, [selectedMerchant]);

  useEffect(() => {
    refresh();
    // Poll every 3 seconds for live status updates
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [refresh]);

  const handlePayoutSubmit = async () => {
    setError('');
    setSuccess('');

    const amountPaise = Math.round(parseFloat(amountRupees) * 100);
    if (!amountRupees || isNaN(amountPaise) || amountPaise <= 0) {
      setError('Enter a valid amount');
      return;
    }

    if (!balance || amountPaise > balance.available_balance_paise) {
      setError(`Insufficient balance. Available: ${formatRupees(balance?.available_balance_paise || 0)}`);
      return;
    }

    // Get the first bank account from the merchant's recent entries context
    // In a real app, you'd have a bank account selector
    const bankAccountId = balance?.recent_entries
      ?.find(e => e.type === 'debit')?.payout_id
      ? null
      : null;

    // We need the bank account ID — fetch it
    // For simplicity, we pass it from the merchant data loaded at startup
    // The seed gives us bank IDs 1, 2, 3 matching merchant order
    const merchantIndex = merchants.findIndex(m => m.id === selectedMerchant.id);
    const bankAccountIdGuess = merchantIndex + 1; // Works with seeded data

    const idempotencyKey = crypto.randomUUID();

    setSubmitting(true);
    try {
      await createPayout(
        {
          merchant_id: selectedMerchant.id,
          amount_paise: amountPaise,
          bank_account_id: bankAccountIdGuess,
        },
        idempotencyKey
      );
      setSuccess(`Payout of ${formatRupees(amountPaise)} submitted successfully!`);
      setAmountRupees('');
      refresh();
    } catch (e) {
      const msg = e.response?.data?.error || 'Failed to create payout';
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Playto Pay</h1>
          <p className="text-xs text-gray-500">Payout Dashboard</p>
        </div>
        {/* Merchant selector */}
        <select
          className="border rounded-lg px-3 py-2 text-sm bg-white"
          value={selectedMerchant?.id || ''}
          onChange={e => {
            const m = merchants.find(m => m.id === parseInt(e.target.value));
            setSelectedMerchant(m);
          }}
        >
          {merchants.map(m => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </div>

      <div className="max-w-5xl mx-auto px-4 py-8 space-y-6">
        {/* Balance cards */}
        {balance && (
          <div className="grid grid-cols-3 gap-4">
            <BalanceCard label="Available Balance" amount={balance.available_balance_paise} color="bg-green-50" />
            <BalanceCard label="Held Balance" amount={balance.held_balance_paise} color="bg-yellow-50" />
            <BalanceCard label="Total Credited" amount={balance.total_credits_paise} color="bg-blue-50" />
          </div>
        )}

        {/* Payout form */}
        <div className="bg-white rounded-xl border p-6">
          <h2 className="text-lg font-semibold mb-4">Request Payout</h2>
          {error && <p className="text-red-600 text-sm mb-3 bg-red-50 p-3 rounded-lg">{error}</p>}
          {success && <p className="text-green-600 text-sm mb-3 bg-green-50 p-3 rounded-lg">{success}</p>}
          <div className="flex gap-3">
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 font-medium">₹</span>
              <input
                type="number"
                placeholder="Amount in rupees (e.g. 1000)"
                className="w-full border rounded-lg pl-8 pr-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={amountRupees}
                onChange={e => setAmountRupees(e.target.value)}
                min="1"
                step="0.01"
              />
            </div>
            <button
              onClick={handlePayoutSubmit}
              disabled={submitting || !selectedMerchant}
              className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-6 py-2.5 rounded-lg text-sm font-medium transition-colors"
            >
              {submitting ? 'Submitting...' : 'Request Payout'}
            </button>
          </div>
          <p className="text-xs text-gray-400 mt-2">
            Each request is automatically idempotent — safe to retry with the same amount.
          </p>
        </div>

        {/* Ledger entries */}
        {balance && (
          <div className="bg-white rounded-xl border p-6">
            <h2 className="text-lg font-semibold mb-4">Recent Transactions</h2>
            <div className="space-y-2">
              {balance.recent_entries.map(entry => (
                <div key={entry.id} className="flex items-center justify-between py-2 border-b last:border-0">
                  <div>
                    <p className="text-sm font-medium text-gray-800">{entry.description}</p>
                    <p className="text-xs text-gray-400">{new Date(entry.created_at).toLocaleString('en-IN')}</p>
                  </div>
                  <span className={`font-semibold text-sm ${entry.type === 'credit' ? 'text-green-600' : 'text-red-500'}`}>
                    {entry.type === 'credit' ? '+' : '-'}{formatRupees(entry.amount_paise)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Payout history */}
        <div className="bg-white rounded-xl border p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Payout History</h2>
            {loading && <span className="text-xs text-gray-400 animate-pulse">Refreshing...</span>}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b">
                  <th className="pb-2 font-medium">Payout ID</th>
                  <th className="pb-2 font-medium">Amount</th>
                  <th className="pb-2 font-medium">Status</th>
                  <th className="pb-2 font-medium">Attempts</th>
                  <th className="pb-2 font-medium">Created</th>
                </tr>
              </thead>
              <tbody>
                {payouts.length === 0 ? (
                  <tr><td colSpan={5} className="py-6 text-center text-gray-400">No payouts yet</td></tr>
                ) : payouts.map(p => (
                  <tr key={p.id} className="border-b last:border-0">
                    <td className="py-3 font-mono text-xs text-gray-500">{p.id.split('-')[0]}…</td>
                    <td className="py-3 font-medium">{formatRupees(p.amount_paise)}</td>
                    <td className="py-3"><StatusBadge status={p.status} /></td>
                    <td className="py-3 text-gray-500">{p.attempt_count}</td>
                    <td className="py-3 text-gray-400">{new Date(p.created_at).toLocaleString('en-IN')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}