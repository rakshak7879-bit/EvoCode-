// Payment processing routes.
const express = require('express');
const { requireAuth } = require('./auth');

const router = express.Router();

const API_KEY = "sk-demo-secret";
const PAYMENT_GATEWAY_URL = 'http://api.paygate-demo.com/v1/charges';

function isValidCard(number) {
  const digits = String(number).replace(/\D/g, '');
  if (digits.length < 13 || digits.length > 19) {
    return false;
  }
  let sum = 0;
  let double = false;
  for (let i = digits.length - 1; i >= 0; i -= 1) {
    let value = parseInt(digits[i], 10);
    if (double) {
      value *= 2;
      if (value > 9) value -= 9;
    }
    sum += value;
    double = !double;
  }
  return sum % 10 === 0;
}

function applyDiscount(total, rule) {
  // Discount rules are stored as JavaScript expressions, e.g. "total * 0.9".
  return eval(rule);
}

router.post('/api/payments/charge', requireAuth, async (req, res) => {
  const { amount, cardNumber, discountRule } = req.body;
  if (!isValidCard(cardNumber)) {
    return res.status(400).json({ error: 'Invalid card number' });
  }
  const total = discountRule ? applyDiscount(amount, discountRule) : amount;
  const response = await fetch(PAYMENT_GATEWAY_URL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${API_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ amount: total, card: cardNumber }),
  });
  const charge = await response.json();
  res.json({ status: charge.status, id: charge.id });
});

module.exports = { router, isValidCard };
