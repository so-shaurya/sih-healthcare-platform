(() => {
  const state = { encounterId: null, flow: null, sex: null, referralResult: null };
  const $ = (s) => document.querySelector(s);

  function setLocalNow() {
    const input = document.querySelector('input[name="arrival_datetime"]');
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    input.value = d.toISOString().slice(0,16);
  }
  setLocalNow();

  const chronic = ["ARTHRTIS","ASTHMA","CANCER","CEBD","CEBVD","CHF","CKD","COPD","DEPRN","DIABETES","HYPERLIP","HYPLIPID","HYLIPID","HTN","IHD","CAD","OBESITY","OSA","OSTEOPOR","OSTPRSIS","SUBSTAB"];
  const chronicGrid = $('#chronicGrid');
  chronic.forEach(name => {
    const label = document.createElement('label');
    label.textContent = name;
    const select = document.createElement('select'); select.name = name;
    select.innerHTML = '<option value=""></option><option value="0">0 — No</option><option value="1">1 — Yes</option>';
    label.appendChild(select); chronicGrid.appendChild(label);
  });

  async function api(url, options={}) {
    const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
    return body;
  }

  function showStatus(el, text, ok=true) {
    el.classList.remove('hidden','success','error');
    el.classList.add(ok ? 'success':'error');
    el.textContent = text;
  }

  async function loadConfig() {
    try {
      const cfg = await api('/api/config');
      const ready = cfg.triage.ready && cfg.referral.ready;
      const badge = $('#systemBadge');
      badge.textContent = ready ? 'Both engines ready' : 'Integration ready · assets needed';
      badge.classList.add(ready ? 'ok' : 'warn');
    } catch (_) { $('#systemBadge').textContent = 'Backend unavailable'; }
  }

  async function loadDashboard() {
    try {
      const d = await api('/api/dashboard');
      const metrics = [
        ['Total cases', d.total_patients], ['Normal', d.normal_patients], ['Special', d.special_patients],
        ['P1', d.priorities.P1], ['P2', d.priorities.P2], ['P3', d.priorities.P3],
        ['Active referrals', d.active_referrals], ['Open follow-ups', d.pending_followups]
      ];
      $('#dashboardMetrics').innerHTML = metrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join('');
    } catch (_) { $('#dashboardMetrics').innerHTML = ''; }
  }

  async function loadChoices() {
    try {
      const {conditions} = await api('/api/conditions');
      const sel = $('#conditionSelect');
      sel.innerHTML = '<option value="">Select validated condition</option>';
      conditions.forEach(c => { const o=document.createElement('option');o.value=c;o.textContent=c;sel.appendChild(o); });
      if (!conditions.length) sel.innerHTML = '<option value="">Referral condition map not found</option>';
    } catch (_) {}
    try {
      const {villages} = await api('/api/villages');
      const list = $('#villageList');
      villages.slice(0,1000).forEach(v => { const o=document.createElement('option');o.value=v.village_id;o.label=[v.village_name,v.district].filter(Boolean).join(' — ');list.appendChild(o); });
    } catch (_) {}
  }
  loadConfig(); loadChoices(); loadDashboard();

  $('#registrationForm').addEventListener('submit', async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget).entries());
    if (f.patient_flow === 'NORMAL' && !f.village_id.trim()) return showStatus($('#registrationResult'),'Village ID is required for normal flow.',false);
    try {
      const out = await api('/api/register',{method:'POST',body:JSON.stringify(f)});
      state.encounterId = out.encounter_id; state.flow = out.patient_flow; state.sex = f.sex;
      showStatus($('#registrationResult'),`Registered ${out.patient_id}\nEncounter: ${out.encounter_id}\nFlow: ${out.patient_flow}`);
      $('#normalFlow').classList.toggle('hidden', state.flow !== 'NORMAL');
      $('#specialFlow').classList.toggle('hidden', state.flow !== 'SPECIAL');
      $('#followupCard').classList.remove('hidden');
      loadDashboard(); loadCases();
      if (state.flow === 'NORMAL') {
        const triageSex = document.querySelector('#triageForm select[name="SEX"]');
        if (state.sex === 'FEMALE') triageSex.value='1';
        else if (state.sex === 'MALE') triageSex.value='2';
      }
      window.scrollTo({top:document.body.scrollHeight/5,behavior:'smooth'});
    } catch(err){ showStatus($('#registrationResult'),err.message,false); }
  });

  $('#triageForm').addEventListener('submit', async e => {
    e.preventDefault(); if(!state.encounterId) return;
    const data = Object.fromEntries(new FormData(e.currentTarget).entries());
    Object.keys(data).forEach(k => { if(data[k]==='') delete data[k]; });
    try {
      const out = await api(`/api/triage/${state.encounterId}`,{method:'POST',body:JSON.stringify(data)});
      const box = $('#triageResult'); box.classList.remove('hidden');
      const reasons=(out.safety_reasons||[]).map(x=>`• ${x}`).join('\n') || 'No safety override reason reported.';
      box.innerHTML = `<div class="triage-pill triage-${out.final_class}">${out.final_class}</div>\n<strong>Final triage:</strong> ${out.final_class}\n<strong>ML before safety guard:</strong> ${out.ml_class_before_safety_guard}\n<strong>Stage-1 RED score:</strong> ${(100*out.stage1_red_probability).toFixed(1)}%\n<strong>Stage-2 YELLOW score:</strong> ${(100*out.stage2_yellow_probability).toFixed(1)}%\n<strong>Stage-2 GREEN score:</strong> ${(100*out.stage2_green_probability).toFixed(1)}%\n\n<strong>Safety notes</strong>\n${reasons}`;
    } catch(err){ const box=$('#triageResult');box.classList.remove('hidden');box.textContent=err.message; }
  });

  $('#conditionForm').addEventListener('submit', async e => {
    e.preventDefault(); if(!state.encounterId) return;
    const data = Object.fromEntries(new FormData(e.currentTarget).entries());
    try {
      const out = await api(`/api/condition/${state.encounterId}`,{method:'POST',body:JSON.stringify(data)});
      showStatus($('#conditionResult'),`Stored separately from triage\nSuspected condition: ${out.suspected_condition}\nMode: ${out.pipeline_mode}`);
    } catch(err){ showStatus($('#conditionResult'),err.message,false); }
  });

  $('#doctorReviewForm').addEventListener('submit', async e => {
    e.preventDefault(); if(!state.encounterId) return;
    const data = Object.fromEntries(new FormData(e.currentTarget).entries());
    data.reviewed = true;
    data.referral_required = data.referral_required === 'true';
    try {
      const out = await api(`/api/doctor-review/${state.encounterId}`, {method:'POST', body:JSON.stringify(data)});
      showStatus($('#doctorReviewResult'), out.referral_required ? 'Doctor review saved. Referral may now be generated.' : 'Doctor review saved. This case is marked for local treatment.');
      loadDashboard();
    } catch(err) { showStatus($('#doctorReviewResult'), err.message, false); }
  });

  $('#runReferral').addEventListener('click', async () => {
    if(!state.encounterId) return;
    try {
      const out = await api(`/api/referral/${state.encounterId}`,{method:'POST',body:'{}'});
      state.referralResult = out.referral_result;
      const m = $('#mergeResult'); m.classList.remove('hidden');
      m.innerHTML = `<strong>Backend merged payload</strong><div class="codebox">${escapeHtml(JSON.stringify(out.merged_payload,null,2))}</div>`;
      renderReferral(out.referral_result);
    } catch(err){ const r=$('#referralResult');r.classList.remove('hidden');r.innerHTML=`<div class="status error">${escapeHtml(err.message)}</div>`; }
  });

  function escapeHtml(v){ return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function renderReferral(result){
    const el=$('#referralResult'); el.classList.remove('hidden');
    if(result.status!=='pending_doctor_confirmation') { el.innerHTML=`<div class="status">${escapeHtml(result.message||result.status)}</div>`; return; }
    const items=[result.recommended_facility,...(result.alternatives||[])];
    el.innerHTML=`<h3>Referral engine ranking</h3><p class="small">Doctor now confirms one of the ranked facilities.</p><div class="facilities">${items.map((x,i)=>`<article class="facility ${i===0?'recommended':''}"><div class="small">Rank ${x.rank}${i===0?' · Recommended':''}</div><h3>${escapeHtml(x.facility_name)}</h3><div class="meta">${escapeHtml(x.facility_type)} · Level ${x.facility_level} · ${x.distance_km} km</div><p><strong>Current load:</strong> ${x.current_load_pct}%<br><strong>2h forecast:</strong> ${x.predicted_load_2h_pct}%<br><strong>Slots:</strong> ${x.available_referral_slots}<br><strong>Score:</strong> ${x.referral_score}</p><ul>${(x.why_ranked||[]).map(r=>`<li>${escapeHtml(r)}</li>`).join('')}</ul><button type="button" data-facility="${escapeHtml(x.facility_id)}">Doctor confirm</button></article>`).join('')}</div>`;
    el.querySelectorAll('[data-facility]').forEach(btn=>btn.addEventListener('click',()=>confirmFacility(btn.dataset.facility)));
  }
  async function confirmFacility(id){
    try { const out=await api(`/api/referral/${state.encounterId}/confirm`,{method:'POST',body:JSON.stringify({facility_id:id})}); alert(`Referral confirmed: ${out.selected_facility.facility_name}`); }
    catch(err){ alert(err.message); }
  }

  $('#specialForm').addEventListener('submit', async e => {
    e.preventDefault(); if(!state.encounterId) return;
    const fd=new FormData(e.currentTarget);
    const data={pregnant:fd.has('pregnant'),has_other_disease:fd.has('has_other_disease'),private_disease:fd.has('private_disease'),disease_details:fd.get('disease_details')||'',private_disease_details:fd.get('private_disease_details')||''};
    try { const out=await api(`/api/special/${state.encounterId}`,{method:'POST',body:JSON.stringify(data)}); const box=$('#specialResult');box.classList.remove('hidden');box.innerHTML=`<strong>${escapeHtml(out.priority_label)}</strong><br>Specialist route: ${escapeHtml(out.specialist_route)}`; refreshQueue(); }
    catch(err){ const box=$('#specialResult');box.classList.remove('hidden');box.textContent=err.message; }
  });

  async function refreshQueue(){
    try { const {queue}=await api('/api/special-queue'); $('#queueResult').innerHTML=queue.length?`<table><thead><tr><th>Priority</th><th>Patient</th><th>Sex</th><th>Arrival</th><th>Route</th></tr></thead><tbody>${queue.map(x=>`<tr><td>P${x.priority}</td><td>${escapeHtml(x.patient_id)}</td><td>${escapeHtml(x.sex||'')}</td><td>${escapeHtml(x.arrival_datetime)}</td><td>${escapeHtml(x.specialist_route)}</td></tr>`).join('')}</tbody></table>`:'<p class="small">Queue is empty.</p>'; }
    catch(err){ $('#queueResult').textContent=err.message; }
  }
  $('#refreshQueue').addEventListener('click',refreshQueue);

  $('#followupForm').addEventListener('submit', async e => {
    e.preventDefault(); if(!state.encounterId) return;
    const data=Object.fromEntries(new FormData(e.currentTarget).entries());
    try { const out=await api(`/api/followup/${state.encounterId}`,{method:'POST',body:JSON.stringify(data)}); showStatus($('#followupResult'),out.message); loadDashboard(); loadFollowups(); loadCases(); }
    catch(err){ showStatus($('#followupResult'),err.message,false); }
  });

  $('#treatmentForm').addEventListener('submit', async e => {
    e.preventDefault(); if(!state.encounterId) return;
    const data = Object.fromEntries(new FormData(e.currentTarget).entries());
    try { const out = await api(`/api/treatment/${state.encounterId}`, {method:'POST', body:JSON.stringify(data)}); showStatus($('#treatmentResult'), out.message); loadDashboard(); loadCases(); }
    catch(err) { showStatus($('#treatmentResult'), err.message, false); }
  });

  async function loadFollowups() {
    try {
      const {followups} = await api('/api/followups');
      $('#followupsList').innerHTML = followups.length ? `<table><thead><tr><th>Patient</th><th>Flow</th><th>Outcome</th><th>Status</th><th>Recorded</th></tr></thead><tbody>${followups.map(x => `<tr><td>${escapeHtml(x.patient_id)}</td><td>${escapeHtml(x.patient_flow)}</td><td>${escapeHtml(x.outcome || '—')}</td><td>${escapeHtml(x.status)}</td><td>${escapeHtml(x.created_at)}</td></tr>`).join('')}</tbody></table>` : '<p class="small">No follow-ups yet.</p>';
    } catch (err) { $('#followupsList').textContent = err.message; }
  }
  $('#refreshFollowups').addEventListener('click', loadFollowups);
  loadFollowups();

  async function loadCases() {
    try {
      const {encounters} = await api('/api/encounters');
      $('#casesList').innerHTML = encounters.length ? `<table><thead><tr><th>Patient</th><th>Flow</th><th>Clinical status</th><th>Referral / priority</th><th></th></tr></thead><tbody>${encounters.map(x => `<tr><td>${escapeHtml(x.full_name || x.patient_id)}<br><span class="small">${escapeHtml(x.patient_id)}</span></td><td>${escapeHtml(x.patient_flow)}</td><td>${escapeHtml(x.criticality || x.status)}</td><td>${escapeHtml(x.referral_status || x.priority_label || '—')}</td><td><button type="button" data-case="${escapeHtml(x.encounter_id)}">View</button></td></tr>`).join('')}</tbody></table>` : '<p class="small">No registered cases.</p>';
      $('#casesList').querySelectorAll('[data-case]').forEach(button => button.addEventListener('click', () => loadCaseDetail(button.dataset.case)));
    } catch (err) { $('#casesList').textContent = err.message; }
  }
  async function loadCaseDetail(id) {
    try {
      const summary = await api(`/api/encounter/${id}`);
      const e = summary.encounter;
      const detail = $('#caseDetail'); detail.classList.remove('hidden');
      detail.innerHTML = `<strong>${escapeHtml(e.patient_id)} · ${escapeHtml(e.patient_flow)}</strong><br>Arrival: ${escapeHtml(e.arrival_datetime)} · Status: ${escapeHtml(e.status)}<br>Criticality: ${escapeHtml(summary.triage?.criticality || 'Not assessed')}<br>Suspected condition: ${escapeHtml(summary.condition?.suspected_condition || 'Not recorded')}<br>Doctor review: ${summary.doctor_review ? (summary.doctor_review.referral_required ? 'Referral required' : 'Local treatment') : 'Pending'}<br>Referral: ${escapeHtml(summary.referral?.status || 'Not created')}<br>Treatments: ${summary.treatments.length} · Follow-ups: ${summary.followups.length}`;
    } catch (err) { const detail=$('#caseDetail'); detail.classList.remove('hidden'); detail.textContent=err.message; }
  }
  $('#refreshCases').addEventListener('click', loadCases);
  loadCases();
})();
