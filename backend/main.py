"""Evo Code backend entry point.

Run from the ``backend/`` directory:

    uvicorn main:app --reload
"""

from app_factory import create_app

app = create_app()
