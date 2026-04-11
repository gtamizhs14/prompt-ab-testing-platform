import hashlib


def assign_variant(user_id: str, variants: list):
    """
    Deterministically assign a user to a variant.

    variants = [
        {"name": "A", "traffic": 50},
        {"name": "B", "traffic": 50}
    ]
    """

    # Step 1: Hash the user_id
    hash_val = hashlib.md5(user_id.encode()).hexdigest()

    # Step 2: Convert to integer
    hash_int = int(hash_val, 16)

    # Step 3: Map to 0–99
    bucket = hash_int % 100

    # Step 4: Assign variant
    cumulative = 0
    for variant in variants:
        cumulative += variant["traffic"]
        if bucket < cumulative:
            return variant["name"]

    return variants[0]["name"]  # fallback