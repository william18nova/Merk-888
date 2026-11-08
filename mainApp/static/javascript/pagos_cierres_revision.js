(() => {
  'use strict';
  const forms = Array.from(document.querySelectorAll('[data-closing-expense-form]'));
  forms.forEach(form => form.addEventListener('submit', event => {
    if (!form.checkValidity()) return;
    if (form.dataset.submitting === 'true') {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = 'true';
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = true;
      button.querySelector('span').textContent = 'Registrando…';
    }
  }));
  window.addEventListener('pageshow', () => forms.forEach(form => {
    delete form.dataset.submitting;
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = false;
      button.querySelector('span').textContent = 'Confirmar y registrar pago';
    }
  }));
  const input = document.querySelector('[data-ledger-search]');
  const output = document.querySelector('[data-ledger-results]');
  if (!input || !output) return;
  const normalize = (value) => String(value).normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const items = Array.from(document.querySelectorAll('[data-ledger-item]'))
    .map((element) => ({ element, text: normalize(element.textContent) }));
  const update = () => {
    const terms = normalize(input.value.trim()).split(/\s+/).filter(Boolean);
    let visible = 0;
    items.forEach(({ element, text }) => {
      const matches = terms.every((term) => text.includes(term)
        || (/^[\d.,]+$/.test(term) && text.replace(/[.,]/g, '').includes(term.replace(/[.,]/g, ''))));
      element.hidden = !matches;
      if (matches) visible += 1;
    });
    output.textContent = `${visible} de ${items.length} pagos visibles. Los totales corresponden a esta lista, antes de buscar.`;
  };
  input.addEventListener('input', update);
  update();
})();
