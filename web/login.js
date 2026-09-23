(() => {
  const form = document.getElementById('login-form');
  const error = document.getElementById('error');

  // Native (no-JavaScript) submit bounces back here with ?error=1; without this the
  // page reloads looking like nothing happened.
  if (new URLSearchParams(window.location.search).get('error') === '1') {
    error.textContent = '用户名或密码不正确';
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.textContent = '';
    const button = form.querySelector('button');
    button.disabled = true;
    try {
      const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ user: form.user.value, pass: form.pass.value }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || '登录失败');
      }
      location.replace('/');
    } catch (err) {
      error.textContent = err.message || '登录失败';
    } finally {
      button.disabled = false;
    }
  });
})();
