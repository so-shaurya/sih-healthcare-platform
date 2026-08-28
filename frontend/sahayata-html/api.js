/* ============================================================
   Sahayata — shared API client (vanilla JS)
   ------------------------------------------------------------
   Point SAHAYATA_API_BASE at your real backend once it's ready.
   Every function tries the real API first; if it's unreachable
   (backend not built yet / offline / CORS not set up), it falls
   back to local mock logic so pages keep working standalone.

   Backend teammates: implement these exact routes + shapes and
   the frontend needs zero further changes.
   ============================================================ */

window.SAHAYATA_API_BASE = window.SAHAYATA_API_BASE || 'http://localhost:8000/api';

async function _call(path, options = {}) {
  const res = await fetch(window.SAHAYATA_API_BASE + path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  if (!res.ok) throw new Error('API ' + path + ' -> ' + res.status);
  return res.json();
}

/* Wraps a real call with a local fallback so the UI never breaks
   while the backend is still being built. Logs which path was used. */
async function withFallback(label, realCall, fallbackFn) {
  try {
    const result = await realCall();
    console.info('[api] '+label+': live backend');
    return result;
  } catch (e) {
    console.warn('[api] '+label+': backend unavailable, using local fallback \u2014', e.message);
    return fallbackFn();
  }
}

const Api = {

  // ---- Auth / patient lookup ----
  // POST /auth/login  { username, password } -> { token, role }
  login(username, password) {
    return _call('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
  },

  // GET /patients/lookup?id=P-xxxx -> patient object (for "returning patient" flow)
  lookupPatient(id, fallbackFn) {
    return withFallback('lookupPatient', () => _call('/patients/lookup?id=' + encodeURIComponent(id)), fallbackFn);
  },

  // ---- 1/2. Patient registration ----
  // POST /patients { name, age, gender, patient_type, location, history } -> { id, ...patient }
  createPatient(patient, fallbackFn) {
    return withFallback('createPatient', () => _call('/patients', { method: 'POST', body: JSON.stringify(patient) }), fallbackFn);
  },

  // ---- 3A. Assessment (vitals + symptoms) ----
  // POST /patients/:id/assessment { temp, bp, hr, spo2, rr, symptoms[], severity, pain, duration, arrival, conditions[], notes, history }
  // -> { assessment_id }
  submitAssessment(patientId, assessment, fallbackFn) {
    return withFallback('submitAssessment',
      () => _call('/patients/' + patientId + '/assessment', { method: 'POST', body: JSON.stringify(assessment) }),
      fallbackFn);
  },

  // ---- 4A/5A. Triage engine (ML) result ----
  // GET /patients/:id/triage -> { level: 'Green'|'Yellow'|'Red', score: number, reasons: string[] }
  getTriage(patientId, fallbackFn) {
    return withFallback('getTriage', () => _call('/patients/' + patientId + '/triage'), fallbackFn);
  },

  // ---- 6A/7A. Doctor review + referral decision ----
  // POST /patients/:id/review { referralRequired: boolean } -> { ok: true }
  submitDoctorReview(patientId, referralRequired, fallbackFn) {
    return withFallback('submitDoctorReview',
      () => _call('/patients/' + patientId + '/review', { method: 'POST', body: JSON.stringify({ referralRequired }) }),
      fallbackFn);
  },

  // ---- 8B. Referral engine — ranked facility matches ----
  // GET /patients/:id/referral-matches -> [{ name, specialty, distance, eta, capacity }, ...] ranked best-first
  getFacilityMatches(patientId, fallbackFn) {
    return withFallback('getFacilityMatches', () => _call('/patients/' + patientId + '/referral-matches'), fallbackFn);
  },

  // ---- 9A/10A. Confirm referral ----
  // POST /patients/:id/referral { facilityName } -> { referralId, facility, reason }
  confirmReferral(patientId, facilityName, fallbackFn) {
    return withFallback('confirmReferral',
      () => _call('/patients/' + patientId + '/referral', { method: 'POST', body: JSON.stringify({ facilityName }) }),
      fallbackFn);
  },

  // ---- 4B/5B. Special-case priority + queue ----
  // GET /patients/:id/priority -> { priority: 1|2|3, label, reason }
  getPriority(patientId, fallbackFn) {
    return withFallback('getPriority', () => _call('/patients/' + patientId + '/priority'), fallbackFn);
  },

  // GET /queue -> [{ name, p, wait }, ...] current specialist queue (for display)
  getQueue(fallbackFn) {
    return withFallback('getQueue', () => _call('/queue'), fallbackFn);
  },

  // ---- 6B. Direct specialist routing ----
  // POST /patients/:id/route-specialist -> { specialist, privacyNote }
  routeSpecialist(patientId, fallbackFn) {
    return withFallback('routeSpecialist', () => _call('/patients/' + patientId + '/route-specialist', { method: 'POST' }), fallbackFn);
  },

  // ---- 11/12/13. Tracking, treatment, follow-up ----
  // GET /patients/:id/tracking -> { steps: [{t,d,done}], treatment: {facility,type,started}, }
  getTracking(patientId, fallbackFn) {
    return withFallback('getTracking', () => _call('/patients/' + patientId + '/tracking'), fallbackFn);
  },

  // POST /patients/:id/treatment-notes { note } -> { ok: true }
  addTreatmentNote(patientId, note, fallbackFn) {
    return withFallback('addTreatmentNote',
      () => _call('/patients/' + patientId + '/treatment-notes', { method: 'POST', body: JSON.stringify({ note }) }),
      fallbackFn);
  },

  // POST /patients/:id/followup { date, outcome, note } -> { ok: true }
  scheduleFollowup(patientId, data, fallbackFn) {
    return withFallback('scheduleFollowup',
      () => _call('/patients/' + patientId + '/followup', { method: 'POST', body: JSON.stringify(data) }),
      fallbackFn);
  },

  // POST /patients/:id/close -> { ok: true }
  closeCase(patientId, fallbackFn) {
    return withFallback('closeCase', () => _call('/patients/' + patientId + '/close', { method: 'POST' }), fallbackFn);
  }
};
