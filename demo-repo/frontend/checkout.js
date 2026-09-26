// Checkout flow: validates the form and submits a charge to the API.

function checkEmail(input) {
  if (!input || typeof input !== 'string') {
    return false;
  }
  const clean = input.trim().toLowerCase();
  const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return emailPattern.test(clean);
}

function validateCard(cardNumber) {
  const digits = String(cardNumber).replace(/\D/g, '');
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

async function sendRequest(path, payload) {
  const token = localStorage.getItem('token');
  const res = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: token ? `Bearer ${token}` : '',
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(`Request failed with status ${res.status}`);
  }
  return res.json();
}

async function submitCheckout(event) {
  event.preventDefault();
  const email = document.querySelector('#checkout-email').value;
  const cardNumber = document.querySelector('#card-number').value;
  const amount = Number(document.querySelector('#amount').value);
  if (!checkEmail(email) || !validateCard(cardNumber)) {
    alert('Please check your email and card number.');
    return;
  }
  const result = await sendRequest('/api/payments/charge', { email, cardNumber, amount });
  document.querySelector('#receipt').textContent = `Payment ${result.status} (id ${result.id})`;
}

document.querySelector('#checkout-form').addEventListener('submit', submitCheckout);
