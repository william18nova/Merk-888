(() => {
  'use strict';
  const lock = form => {
    if (form.dataset.sending) return false;
    form.dataset.sending = 'true';
    form.setAttribute('aria-busy', 'true');
    const button = form.querySelector('button[type="submit"]');
    if (button) { button.disabled = true; button.textContent = 'Verificando…'; }
    return true;
  };
  document.querySelectorAll('[data-sync-form]').forEach(form => form.addEventListener('submit', event => {
    if (!lock(form)) event.preventDefault();
  }));
  const form = document.querySelector('[data-sync-review]');
  const dialog = document.getElementById('sync-confirm');
  if (form && dialog && typeof dialog.showModal === 'function') {
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (form.dataset.sending) return;
      dialog.returnValue = 'cancel';
      dialog.showModal();
    });
    dialog.addEventListener('close', () => {
      if (dialog.returnValue === 'confirm' && form.reportValidity() && lock(form)) {
        HTMLFormElement.prototype.submit.call(form);
      }
    });
  }
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
})();
