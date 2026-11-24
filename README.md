# SDS Chemical Compatibility Advisor
CAT1-037 · Local SDS review application, in development.

## Run the Phase 0 skeleton
With Python 3.11 or later, install the dependencies in a virtual environment.
```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open http://localhost:8000. The first launch creates advisor.db.
The setup milestone contains no chemical verdict engine.

Decision support only. Confirm with a qualified person.
