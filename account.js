'use strict';
const el = id => document.getElementById(id);
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function request(path, data) {
  const r = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await r.json();
  if (r.status === 401 && !el('loginForm')) location.assign('/login');
  if (!r.ok) throw Error(result.error || 'Não foi possível concluir.');
  return result;
}
function message(error) { el('message').textContent = error.message || String(error); }
if (el('loginForm')) {
  el('loginForm').onsubmit = async event => {
    event.preventDefault(); const form=event.currentTarget; const button=form.querySelector('button'); button.disabled=true;
    try { await request('/api/login', Object.fromEntries(new FormData(form))); location.assign('/'); }
    catch(error) { message(error); form.elements.password.value=''; }
    finally { button.disabled=false; }
  };
}
if (el('userForm')) {
  let users=[], before='', resetId=null;
  async function loadUsers() {
    users=await request('/api/users');
    el('users').innerHTML='<table><thead><tr><th>Usuário</th><th>Perfil</th><th>Estado</th><th>Ações</th></tr></thead><tbody>'+users.map(u=>`<tr><td>${safe(u.username)}</td><td>${u.role==='admin'?'Administrador':'Visualizador'}</td><td>${u.active?'Ativo':'Desativado'}</td><td><button data-id="${u.id}" data-action="toggle">${u.active?'Desativar':'Ativar'}</button><button data-id="${u.id}" data-action="role">${u.role==='admin'?'Tornar visualizador':'Tornar administrador'}</button><button data-id="${u.id}" data-action="password">Redefinir senha</button></td></tr>`).join('')+'</tbody></table>';
    const actor=el('actor').value;
    el('actor').innerHTML='<option value="">Todos</option>'+users.map(u=>`<option value="${safe(u.username)}">${safe(u.username)}</option>`).join('');
    el('actor').value=actor;
  }
  const labels={'login':'Entrada','logout':'Saída','painel.aberto':'Abriu o painel','usuario.criado':'Criou usuário','usuario.atualizado':'Alterou usuário / revogou sessões','senha.alterada':'Alterou a própria senha','impressora.salvar':'Salvou cadastro de impressora','rede.buscar':'Solicitou busca na rede','bambu.abrir':'Solicitou abertura do Bambu Studio','interface.camera.ampliada':'Ampliou câmera (evento da interface)','acesso.recusado':'Acesso recusado'};
  async function loadAudit(reset=true) {
    if(reset) before='';
    const rows=await request('/api/audit?actor='+encodeURIComponent(el('actor').value)+(before?'&before='+before:''));
    el('audit').innerHTML='<table><thead><tr><th>Quando</th><th>Usuário</th><th>Ação</th><th>Alvo</th><th>Resultado</th></tr></thead><tbody>'+rows.map(r=>`<tr><td>${safe(new Date(r.at*1000).toLocaleString('pt-BR'))}</td><td>${safe(r.actor)}</td><td>${safe(labels[r.action]||r.action)}</td><td>${safe(r.target)}</td><td>${safe(r.result)}</td></tr>`).join('')+'</tbody></table>'+(rows.length?'':'<p>Nenhum registro neste filtro.</p>');
    before=rows.at(-1)?.id||''; el('older').disabled=rows.length<100;
  }
  el('userForm').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;const button=form.querySelector('button');button.disabled=true;try{await request('/api/users',Object.fromEntries(new FormData(form)));form.reset();message('Conta criada.');await loadUsers();await loadAudit();}catch(e){message(e)}finally{button.disabled=false}};
  el('users').onclick=async event=>{const b=event.target.closest('[data-action]');if(!b)return;const u=users.find(u=>u.id===Number(b.dataset.id));if(b.dataset.action==='password'){resetId=u.id;el('resetName').textContent=u.username;el('resetForm').reset();el('resetDialog').showModal();return}const change=b.dataset.action==='toggle'?{active:!u.active}:{role:u.role==='admin'?'viewer':'admin'};if(!confirm('Alterar '+u.username+' e encerrar suas sessões atuais?'))return;try{await request('/api/users/'+u.id,change);await loadUsers();await loadAudit()}catch(e){message(e)}};
  el('resetForm').onsubmit=async event=>{event.preventDefault();try{await request('/api/users/'+resetId,Object.fromEntries(new FormData(event.currentTarget)));el('resetDialog').close();event.currentTarget.reset();message('Senha redefinida. Sessões encerradas.');await loadAudit()}catch(e){el('resetDialog').close();message(e)}};
  el('cancelReset').onclick=()=>el('resetDialog').close();
  el('reloadAudit').onclick=()=>loadAudit().catch(message);el('actor').onchange=()=>loadAudit().catch(message);el('older').onclick=()=>loadAudit(false).catch(message);
  loadUsers().then(()=>loadAudit()).catch(message);
}
