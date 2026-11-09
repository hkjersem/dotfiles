from .cache_keys import cache_key


def get_profile(cache: dict[str, str], tenant_id: str, profile_id: str) -> str | None:
    return cache.get(cache_key(tenant_id, profile_id))
