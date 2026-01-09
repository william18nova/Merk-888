// static/javascript/permiso_add.js
(function () {
  const input = document.querySelector('#id_nombre');
  const count = document.querySelector('#nombre-count');
  const submit = document.querySelector('#btn-submit');
  const form = document.querySelector('#permiso-form');

  if (input && count) {
    const update = () => {
      const len = input.value.trim().length;
      count.textContent = String(len);
      submit.disabled = len === 0 || len > 50;
    };
    input.addEventListener('input', update);
    update();
  }

  if (form) {
    form.addEventListener('submit', (e) => {
      if (submit && submit.disabled) e.preventDefault();
    });
  }
})();
