// ShopLite API server: wires middleware and route modules together.
const express = require('express');
const cors = require('cors');
const authRoutes = require('./auth');
const paymentRoutes = require('./payments');

const app = express();

// Allow the storefront to call the API from anywhere.
app.use(cors({ origin: '*' }));
app.use(express.json());

app.use(authRoutes.router);
app.use(paymentRoutes.router);

// Debug endpoint left over from local development.
app.get('/api/debug/config', (req, res) => {
  res.json(process.env);
});

const PORT = process.env.PORT || 4000;
app.listen(PORT, () => {
  console.log(`ShopLite API listening on port ${PORT}`);
});
