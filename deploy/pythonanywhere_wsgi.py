"""Copy to the PythonAnywhere WSGI configuration, then replace the two paths."""
import sys
from dotenv import load_dotenv

project_path = "/home/YOUR_USERNAME/bar_pro"
if project_path not in sys.path:
    sys.path.insert(0, project_path)
# Keep this file outside the checkout; use the same file for migration commands.
load_dotenv("/home/YOUR_USERNAME/.config/bar_pro/production.env", override=False)
from app import create_app
application = create_app("production")
