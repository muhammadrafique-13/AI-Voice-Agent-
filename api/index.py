"""Vercel serverless entrypoint.

Vercel's @vercel/python runtime looks for a module-level ASGI callable named `app`.
vercel.json rewrites every path here, so FastAPI keeps full control of routing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402

__all__ = ["app"]
