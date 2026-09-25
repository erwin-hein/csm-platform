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
