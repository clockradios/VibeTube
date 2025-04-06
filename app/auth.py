import os
from datetime import datetime, timedelta
from typing import Optional
import secrets
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import FastAPI
from starlette.responses import RedirectResponse

# Load credentials from environment variables
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ytdlp_admin_password")

# Security schemes
security = HTTPBasic()

# In-memory session store (for a simple implementation)
# In a production app, you'd want to use a more robust solution
sessions = {}
SESSION_EXPIRY = 24  # Hours

def create_session(username: str) -> str:
    """Create a new session for a user"""
    session_id = secrets.token_urlsafe(32)
    expiry = datetime.now() + timedelta(hours=SESSION_EXPIRY)
    sessions[session_id] = {"username": username, "expiry": expiry}
    return session_id

def validate_session(session_id: str) -> bool:
    """Check if a session is valid"""
    if session_id not in sessions:
        return False
    
    session = sessions[session_id]
    if datetime.now() > session["expiry"]:
        # Session expired
        del sessions[session_id]
        return False
    
    return True

def get_admin_user(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Validate admin credentials"""
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    
    return credentials.username

def get_session_username(request: Request) -> Optional[str]:
    """Get the username from the session cookie"""
    session_id = request.cookies.get("session")
    if not session_id or not validate_session(session_id):
        return None
    
    return sessions[session_id]["username"]

class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to check authentication for protected routes"""
    
    def __init__(self, app: FastAPI, exclude_paths: list = None):
        super().__init__(app)
        self.exclude_paths = exclude_paths or []
        # Always exclude login and static paths
        self.exclude_paths.extend(["/login", "/logout", "/static", "/downloads"])
    
    async def dispatch(self, request: Request, call_next):
        # Skip authentication for excluded paths
        path = request.url.path
        if any(path.startswith(excluded) for excluded in self.exclude_paths):
            return await call_next(request)
        
        # Check session
        username = get_session_username(request)
        if username is None:
            # Redirect to login page
            return RedirectResponse(url="/login", status_code=302)
        
        # User is authenticated, proceed
        return await call_next(request)

# Function to setup authentication in the main app
def setup_auth(app: FastAPI):
    """Setup authentication middleware and routes"""
    
    # Add middleware
    app.add_middleware(
        AuthMiddleware,
        exclude_paths=[
            "/login", 
            "/logout", 
            "/static", 
            "/downloads",
            # Add any other paths that should be publicly accessible
        ]
    ) 