def mask_name(user) -> str:
    """'Alex Mwangi' -> 'A*** M*****'. Enough to confirm a recipient without exposing identities."""
    full = (user.get_full_name() or "").strip() or user.get_username()
    return " ".join(part[0] + "*" * min(len(part) - 1, 5) for part in full.split())