// Collapsible deliverables: remember what the user toggled for this browser session, so an
// htmx refresh of the panel doesn't undo it. Defaults (finished = collapsed) come from the server.
function dvState() { try { return JSON.parse(sessionStorage.getItem('dv') || '{}'); } catch (e) { return {}; } }
function dvSave(s) { try { sessionStorage.setItem('dv', JSON.stringify(s)); } catch (e) {} }
function dvToggle(btn) {
  const el = btn.closest('.dv'); el.classList.toggle('collapsed');
  const s = dvState(); s[el.dataset.dv] = el.classList.contains('collapsed') ? 'c' : 'o'; dvSave(s);
}
function dvAll(collapse) {
  const s = dvState();
  document.querySelectorAll('.dv[data-dv]').forEach(el => { el.classList.toggle('collapsed', collapse); s[el.dataset.dv] = collapse ? 'c' : 'o'; });
  dvSave(s);
}
function dvRestore(root) {
  const s = dvState();
  (root || document).querySelectorAll('.dv[data-dv]').forEach(el => {
    if (s[el.dataset.dv] === 'c') el.classList.add('collapsed');
    if (s[el.dataset.dv] === 'o') el.classList.remove('collapsed');
  });
}
document.addEventListener('DOMContentLoaded', () => dvRestore());
document.addEventListener('htmx:afterSettle', e => dvRestore(e.target));

// Deliverable chat pop-up.
function openChat() {
  const dlg = document.getElementById('chat-dialog'); if (!dlg) return;
  document.getElementById('chat-dialog-body').innerHTML = '<div class="empty">Loading…</div>';
  if (!dlg.open) dlg.showModal();
}
document.addEventListener('click', e => {
  const dlg = document.getElementById('chat-dialog');
  if (dlg && e.target === dlg) dlg.close();   // click on the backdrop closes it
});

// Opportunities: moving to Closed Lost asks for the reason the server requires.
function oppLostReason(form) {
  const reason = prompt('Why was it lost?');
  if (!reason) return false;
  form.querySelector('[name=lost_reason]').value = reason;
  return true;
}
function oppStage(sel) {
  if (sel.value === 'closed_lost' && !oppLostReason(sel.form)) {
    sel.value = sel.querySelector('option[selected]').value;
    return;
  }
  sel.form.requestSubmit();
}
// New opportunity: show the product's default price and unit.
function npPrice(sel) {
  const o = sel.selectedOptions[0], price = document.getElementById('np-price');
  price.placeholder = o && o.dataset.price ? o.dataset.price : 'required';
  document.getElementById('np-unit').textContent = o && o.dataset.unit ? '(' + o.dataset.unit + 's)' : '';
}
