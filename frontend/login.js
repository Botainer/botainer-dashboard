import { getBearer, setBearer, clearBearer, authHeaders } from './auth.js';

const form = document.getElementById('login-form');
// A paired browser may land on /unlock from an old bookmark or another tab.
// Validate both existing secrets; never recover a bearer from a cookie alone.
const remembered = getBearer();
if (remembered) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  fetch('/api/state', { credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: authHeaders(remembered), signal: controller.signal })
    .then(response => {
      if (response.ok || response.status === 409) location.replace('/');
      else if (response.status === 401) clearBearer();
    }).catch(() => {}).finally(() => clearTimeout(timeout));
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  const input = form.elements.namedItem('token');
  const button = form.querySelector('button');
  const error = document.getElementById('login-error');
  error.textContent = '';
  button.disabled = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const token = input.value;
    input.value = '';
    const response = await fetch('/auth', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ token }), signal: controller.signal,
    });
    if (!response.ok) throw new Error('Pairing failed. The code may have been used or expired. Run the command shown above again, then enter the code it displays. No dashboard restart is needed.');
    setBearer((await response.json()).bearer);
    location.replace('/');
  } catch (failure) {
    error.textContent = failure.name === 'AbortError' ? 'The local service did not respond.' : failure.message;
  } finally { clearTimeout(timeout); button.disabled = false; }
});
