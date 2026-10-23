"""Shared helpers for PrimarySource direct-mode tests."""


def sha256_hex(data):
    import hashlib
    if isinstance(data, str):
        data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()
