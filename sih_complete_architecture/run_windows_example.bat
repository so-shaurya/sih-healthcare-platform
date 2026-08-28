@echo off
REM Optional variables: set these before calling this script if assets are outside this folder.
REM set "TRIAGE_BUNDLE_PATH=D:\secure-assets\triage_safety_pipeline_v6.pkl"
REM set "REFERRAL_DATA_DIR=D:\secure-assets\referral-data"

if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
python app.py
