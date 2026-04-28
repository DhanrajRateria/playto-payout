import axios from 'axios';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  headers: { 'Content-Type': 'application/json' },
});

export const getMerchants = () => api.get('/merchants/');

export const getMerchantBalance = (merchantId) =>
  api.get(`/merchants/${merchantId}/balance/`);

export const getPayouts = (merchantId) =>
  api.get(`/payouts/list/?merchant_id=${merchantId}`);

export const createPayout = (data, idempotencyKey) =>
  api.post('/payouts/', data, {
    headers: { 'Idempotency-Key': idempotencyKey },
  });

export const getPayoutStatus = (payoutId) =>
  api.get(`/payouts/${payoutId}/`);