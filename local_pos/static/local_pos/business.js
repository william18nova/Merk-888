(() => {
  'use strict';
  const money = n => Number(n).toLocaleString('es-CO', {minimumFractionDigits:2,maximumFractionDigits:2});
  const expense = document.getElementById('local-expense-form');
  if (expense) {
    const amount = expense.querySelector('[data-money-input]'), method = expense.elements.method;
    function preview() {
      let raw = amount.value.replace(/[^0-9,]/g, '').split(',');
      const whole = (raw[0] || '').replace(/^0+(?=\d)/,'');
      amount.value = whole.replace(/\B(?=(\d{3})+(?!\d))/g,'.') + (raw.length>1 ? ','+raw[1].slice(0,2):'');
      const base = Number(whole+'.'+(raw[1] || '0'));
      const enabled = method.selectedOptions[0]?.dataset.tax === 'true';
      expense.elements.amount_base.value = base.toFixed(2);
      expense.elements.expected_tax.value = String(enabled);
      const cents = Math.round(base*100), tax = enabled ? Math.round(cents*0.004) : 0;
      document.getElementById('local-tax-preview').textContent = `4 × 1.000: $ ${money(tax/100)} · Total pagado: $ ${money((cents+tax)/100)}`;
    }
    amount.addEventListener('input',preview); method.addEventListener('change',preview); preview();
    expense.elements.concept.addEventListener('blur', e => e.target.value=e.target.value.trim().replace(/\s+/g,' ').toUpperCase());
  }
  const returns = document.getElementById('local-return-form');
  if (returns) {
    const snapshot = JSON.parse(document.getElementById('local-return-snapshot').textContent).sale;
    const gross = snapshot.items.reduce((sum,row)=>sum+Number(row.unit_price)*row.quantity,0);
    function preview() {
      let partial = snapshot.items.reduce((sum,row)=>sum+Number(row.unit_price)*Number(returns.elements['qty_'+row.detail_id].value),0);
      if (gross>0 && Number(snapshot.total)<gross) partial=partial*Number(snapshot.total)/gross;
      partial=Math.min(Number(snapshot.total),partial);
      returns.elements.expected_total.value=partial.toFixed(2);
      document.getElementById('return-total').textContent='Total a devolver: $ '+money(partial);
    }
    returns.addEventListener('input',preview);preview();
  }
  const dialog=document.getElementById('business-confirm');
  for(const form of document.querySelectorAll('[data-business-confirm]')) form.addEventListener('submit',event=>{
    if(form.dataset.confirmed==='yes') return;
    event.preventDefault();document.getElementById('business-confirm-text').textContent=form.dataset.confirm;
    dialog.returnValue='';dialog.showModal();
    dialog.addEventListener('close',()=>{if(dialog.returnValue==='confirm'){form.dataset.confirmed='yes';form.requestSubmit();}}, {once:true});
  });
})();
