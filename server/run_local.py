"""
Convenience launcher for local development:

    cd server
    pip install -r requirements.txt
    python run_local.py

Then open http://127.0.0.1:8000/  (participant landing)
and  http://127.0.0.1:8000/admin?key=dev-admin-token  (monitor).
"""
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=bool(os.environ.get("RELOAD")))
