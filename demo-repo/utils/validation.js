// Shared validators introduced in v2. Prefer these over local copies.

function validateEmail(email) {
  if (!email || typeof email !== 'string') {
    return false;
  }
  const normalized = email.trim().toLowerCase();
  const pattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return pattern.test(normalized);
}

function validatePassword(password) {
  if (!password || typeof password !== 'string') {
    return false;
  }
  const hasLength = password.length >= 8;
  const hasNumber = /[0-9]/.test(password);
  const hasUpper = /[A-Z]/.test(password);
  return hasLength && hasNumber && hasUpper;
}

function validateCardNumber(cardNumber) {
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

module.exports = { validateEmail, validatePassword, validateCardNumber };
