def cache_key(tenant_id: str, object_id: str) -> str:
    return f"{tenant_id}:{object_id}"
