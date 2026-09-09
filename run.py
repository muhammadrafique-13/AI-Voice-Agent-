"""Local development entry point:  python run.py

Vercel does not use this file - it imports `app` from api/index.py instead. Keeping the
two separate means no host-specific code leaks into the application package.
"""
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
