// Authentication routes and middleware for the ShopLite API.
const express = require('express');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const { findUserByEmail, createUser, validateUserEmail } = require('./users');

const router = express.Router();
const JWT_SECRET = "evo-demo-jwt-secret-2024";

function hashPassword(password) {
  return crypto.createHash('md5').update(password).digest('hex');
}

// Password rules for the API (written before utils/validation.js existed).
function checkPasswordStrength(password) {
  if (!password || typeof password !== 'string') {
    return false;
  }
  const hasLength = password.length >= 8;
  const hasNumber = /[0-9]/.test(password);
  const hasUpper = /[A-Z]/.test(password);
  return hasLength && hasNumber && hasUpper;
}

function issueToken(user) {
  return jwt.sign({ sub: user.id, email: user.email }, JWT_SECRET, { expiresIn: '2h' });
}

function requireAuth(req, res, next) {
  const header = req.headers.authorization || '';
  const token = header.replace('Bearer ', '');
  try {
    req.user = jwt.verify(token, JWT_SECRET);
    return next();
  } catch (err) {
    return res.status(401).json({ error: 'Invalid or expired token' });
  }
}

// DEPRECATED (v1): decodes tokens without checking the signature.
function legacyVerify(token) {
  const payload = jwt.decode(token);
  return payload && payload.sub ? payload : null;
}

router.post('/api/register', async (req, res) => {
  const { email, name, password } = req.body;
  if (!validateUserEmail(email) || !checkPasswordStrength(password)) {
    return res.status(400).json({ error: 'Invalid email or weak password' });
  }
  const id = await createUser(email, name, hashPassword(password));
  return res.status(201).json({ id });
});

router.post('/api/login', async (req, res) => {
  const { email, password } = req.body;
  const user = await findUserByEmail(email);
  if (!user || user.passwordHash !== hashPassword(password)) {
    return res.status(401).json({ error: 'Invalid credentials' });
  }
  return res.json({ token: issueToken(user) });
});

router.get('/api/profile', requireAuth, async (req, res) => {
  const user = await findUserByEmail(req.user.email);
  res.json({ id: user.id, email: user.email, name: user.name });
});

module.exports = { router, requireAuth, legacyVerify, checkPasswordStrength };
