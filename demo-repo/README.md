# ShopLite

ShopLite is a small e-commerce store: a browser frontend for login and checkout, an Express API, and a PostgreSQL database.

> This repository is intentionally vulnerable. It exists to demonstrate Evo Code. Do not deploy it.

## Architecture

Browser (`frontend/*.js`) → Express API (`backend/server.js`) → route modules (`backend/auth.js`, `backend/payments.js`) → data access (`backend/users.js`) → PostgreSQL.

## API

| Method | Path                   | Handler               |
| ------ | ---------------------- | --------------------- |
| POST   | /api/register          | backend/auth.js       |
| POST   | /api/login             | backend/auth.js       |
| GET    | /api/profile           | backend/auth.js       |
| POST   | /api/payments/charge   | backend/payments.js   |

## Running locally

```bash
npm install
DATABASE_URL=postgres://localhost/shoplite npm start
```

## Engineering history

- v1 (2023): tokens were checked with `legacyVerify` in backend/auth.js, which decodes JWTs without verifying them. It is deprecated but still present.
- v1 used raw SQL string building in `legacyFindUser` (backend/users.js). It was replaced by the parameterized `findUserByEmail` in v2 and is scheduled for removal.
- v2 (2024): shared validators were added in utils/validation.js. The copies in frontend/auth.js and frontend/checkout.js predate them and were never migrated.
