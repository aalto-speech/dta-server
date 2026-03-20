import os

from dotenv import load_dotenv

load_dotenv()

# Configuration settings for the DTA server application.
PYTHON_ENV = os.getenv("PYTHON_ENV", "development")

DATABASE = "development.db"  # default to development DB

if PYTHON_ENV == "production":
    DATABASE = os.getenv("DATABASE", "dta.db")
elif PYTHON_ENV == "test":
    DATABASE = "test.db"

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
