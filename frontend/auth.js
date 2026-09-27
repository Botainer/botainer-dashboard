// This secret belongs to this browser origin (including port). Cookies alone
// cannot authorize API or terminal requests from another local web application.
// Persistent storage allows a paired browser to reopen tabs/restart the service.
// The server persists only hashes and still requires the independent HttpOnly
// cookie. Lock revokes the pair server-side before removing this half locally.
const KEY = 'botainer-dashboard:bearer:v2';
const VALID = /^[A-Za-z0-9_-]{43,128}$/;

export function getBearer(storage) {
  try {
    if (storage === undefined) storage = globalThis.localStorage;
    const value = storage?.getItem(KEY);
    return typeof value === 'string' && VALID.test(value) ? value : null;
  } catch { return null; }
}

export function setBearer(value, storage = globalThis.localStorage) {
  if (typeof value !== 'string' || !VALID.test(value)) throw new Error('Invalid dashboard credential.');
  // Refuse to proceed if origin-scoped storage is unavailable; no cookie-only fallback.
  storage.setItem(KEY, value);
  if (getBearer(storage) !== value) throw new Error('This browser must allow local storage for dashboard pairing.');
}

export function clearBearer(storage) {
  try {
    if (storage === undefined) storage = globalThis.localStorage;
    storage?.removeItem(KEY);
  } catch { /* Requests still require a valid server credential. */ }
}

export function authHeaders(bearer = getBearer()) {
  if (typeof bearer !== 'string' || !VALID.test(bearer)) throw new Error('Dashboard locked. Pair this browser.');
  return { Authorization: `Bearer ${bearer}` };
}
