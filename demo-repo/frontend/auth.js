// Login form logic for the ShopLite storefront (v1 client code).

function isValidEmail(value) {
  if (!value || typeof value !== 'string') {
    return false;
  }
  const trimmed = value.trim().toLowerCase();
  const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return re.test(trimmed);
}

async function postJSON(url, body) {
  const token = localStorage.getItem('token');
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: token ? `Bearer ${token}` : '',
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  return response.json();
}

async function handleLogin(event) {
  event.preventDefault();
  const email = document.querySelector('#email').value;
  const password = document.querySelector('#password').value;
  const status = document.querySelector('#login-status');
  if (!isValidEmail(email)) {
    status.textContent = 'Please enter a valid email address.';
    return;
  }
  try {
    const data = await postJSON('/api/login', { email, password });
    localStorage.setItem('token', data.token);
    status.innerHTML = `Welcome back, ${email}!`;
  } catch (err) {
    status.textContent = 'Login failed.';
  }
}

document.querySelector('#login-form').addEventListener('submit', handleLogin);
