import os

from supabase import Client, create_client

_REQUIRED = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")


def get_client() -> Client:
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in, or export them in your shell. "
            "See the README's local-setup section to run without cloud credentials."
        )
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
