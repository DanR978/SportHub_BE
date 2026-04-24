"""
Shared slowapi limiter. Imported by main.py (to register middleware) and by
routers (to decorate endpoints). Keys requests by client IP via X-Forwarded-For
when behind a proxy.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
