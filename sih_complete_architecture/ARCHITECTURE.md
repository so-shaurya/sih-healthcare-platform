# SIH Integrated Prototype Architecture

## Core rule

The uploaded **triage engine** and **referral engine** are treated as frozen modules. The integration layer never forwards symptom/referral metadata into the triage model. The two normal-flow pipelines meet only in the backend merger immediately before `ReferralEngine.recommend(...)`.

## End-to-end flow

```text
ASHA / USER WEB
      |
      v
PATIENT REGISTRATION
patient_id, patient_flow, sex, village_id, arrival_datetime
      |
      v
   SQLite DB
      |
      +-----------------------------+
      |                             |
      v                             v
  NORMAL FLOW                   SPECIAL FLOW
      |                             |
      |                    P1 Pregnant + disease
      |                    P2 Pregnant only
      |                    P3 Private disease (male/female)
      |                             |
      |                      Priority Queue
      |                    priority -> arrival time
      |                             |
      |                      Specialist route
      |                             |
      +-----------+                 |
                  |                 |
        +---------+---------+       |
        |                   |       |
        v                   v       |
 TRIAGE INPUTS          SYMPTOMS    |
        |                   |       |
        v                   v       |
 FROZEN TRIAGE       CONDITION      |
    ENGINE            PIPELINE      |
        |                   |       |
        v                   v       |
 RED/YELLOW/GREEN   suspected_condition
        |                   |
        +---------+---------+
                  |
                  v
              DATABASE
                  |
        Persisted doctor review gate
        (prototype default is visible and editable)
                  |
                  v
           BACKEND MERGER
                  |
                  v
 {
   patient_id,
   patient_flow,
   doctor_reviewed: true,
   referral_required: true,
   criticality,             <- triage engine only
   suspected_condition,     <- condition pipeline only
   patient_location: {village_id},
   arrival_datetime
 }
                  |
                  v
        FROZEN REFERRAL ENGINE
                  |
       condition -> capability
       village_id -> villages.csv
       village coordinates -> Haversine distance
       facility capability / criticality fit
       current load
       trained 2-hour load forecast
       capacity
                  |
                  v
           RANKED FACILITIES
                  |
                  v
           DOCTOR CONFIRMS
                  |
                  v
            REFERRAL CREATED
                  |
                  +------------------+
                                     |
SPECIAL SPECIALIST ROUTE ------------+
                                     v
                               UNIFIED FOLLOW-UP
```

## Backend ownership

- `/api/register`: creates an encounter.
- `/api/triage/<encounter_id>`: strips input to triage-only fields and calls `predict_patient(...)`.
- `/api/condition/<encounter_id>`: stores the parallel symptom pipeline output. Until that model is supplied, a validated `suspected_condition` is accepted as the pipeline output.
- `/api/referral/<encounter_id>`: **the merger**. It fetches registration + triage + condition data and the persisted doctor-review gate from the database, creates the referral JSON, then calls `ReferralEngine.recommend(...)`.
- `/api/doctor-review/<encounter_id>`: persists the doctor review and referral decision. A transparent prototype auto-review is created after condition output when no doctor decision exists; a doctor can replace it before referral.
- `/api/referral/<encounter_id>/confirm`: doctor confirms one of the ranked facilities.
- `/api/special/<encounter_id>`: assigns P1/P2/P3 and specialist route.
- `/api/special-queue`: P1 before P2 before P3; FIFO by arrival time within the same priority.
- `/api/followup/<encounter_id>`: common follow-up destination for normal and special flows.
- `/api/treatment/<encounter_id>`: stores treatment/consultation notes for either flow before follow-up.

## Separation guarantee

The backend has an explicit `TRIAGE_FIELDS` allow-list. Fields such as `symptoms`, `suspected_condition`, `patient_id`, `village_id`, `doctor_reviewed`, and `referral_required` are not sent to the triage engine.

## Special priority policy implemented

1. **P1 — Pregnant + disease**: highest priority; Gynaecologist/Obstetrician + relevant disease specialist.
2. **P2 — Pregnant only**: second priority; Gynaecologist/Obstetrician.
3. **P3 — Private/sensitive disease**: third priority; applies to male or female; appropriate private-disease specialist.

Same-priority patients are ordered by arrival time.
