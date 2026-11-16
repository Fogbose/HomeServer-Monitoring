'use strict';
document.getElementById('logout').addEventListener('click',async()=>{
 const status=document.getElementById('status');status.textContent='Fermeture de la connexion…';
 try{
  await fetch('/api/Logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',credentials:'same-origin'});
  const state=await fetch('/auth/api/state',{credentials:'same-origin'});
  const headers={'Content-Type':'application/json'};
  const csrf=state.headers.get('X-CSRF-Token');if(csrf)headers['X-CSRF-Token']=csrf;
  const result=await fetch('/auth/api/logout',{method:'POST',headers,body:'{}',credentials:'same-origin'});
  if(!result.ok)throw Error('La déconnexion centrale n’a pas été confirmée.');
  localStorage.removeItem('pocketbase_auth');
  location.assign('/auth/');
 }catch(error){status.textContent=error.message;}
});
