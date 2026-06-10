from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_ip(request):
    # Behind the rproxy nginx, the real client IP is in X-Real-IP.
    # Fall back to X-Forwarded-For first hop, then socket peer.
    xri = request.headers.get('x-real-ip')
    if xri:
        return xri
    xff = request.headers.get('x-forwarded-for')
    if xff:
        return xff.split(',')[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip)
