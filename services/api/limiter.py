from slowapi import Limiter
from slowapi.util import get_remote_address
from settings import settings

# Instance partagée — importée dans main.py ET dans les routers qui ont besoin
# de limites spécifiques (ex: /auth/token à 5/minute).
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit],
)
