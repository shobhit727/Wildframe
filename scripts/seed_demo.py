"""Seed the Wildframe local stack with demo data so the UI has real content.

Hits each service through the gateway (TLS via Caddy):

  auth-service      8000  -> /api/v1/auth/...
  content-service   8000  -> /api/v1/...

Creates: genres, a demo user, ~10 movies, 3 shows with seasons+episodes.
Posters/backdrops use picsum.photos placeholder images so the UI renders.

Demo credentials are environment-driven. Set WILDFRAME_DEMO_EMAIL /
WILDFRAME_DEMO_PASSWORD (or DEMO_EMAIL / DEMO_PASSWORD / DEMO_PASS) before
running. If no password is provided a random one is generated and printed
once.

Production guard: refuses to run when ENVIRONMENT=production or when
DATABASE_URL points to a non-disposable host unless DEV_SEED_ALLOWED=true.
"""

import os
import secrets
import sys
from urllib.parse import urlparse

import httpx

GATEWAY = os.getenv("WF_API_URL") or os.getenv("WILDFRAME_GATEWAY") or "https://localhost:8000"
DEMO_USER_ID = "e4019888-fc5b-4264-9952-39c44f869686"
AUTH = f"{GATEWAY}/auth"
CONTENT = f"{GATEWAY}/content"

BOLD, RED, GREEN, YELLOW, END = "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"


def get_demo_email() -> str:
    return os.getenv("WILDFRAME_DEMO_EMAIL") or os.getenv("DEMO_EMAIL") or "demo@wildframe.com"


def get_demo_password() -> str:
    pwd = (
        os.getenv("WILDFRAME_DEMO_PASSWORD") or os.getenv("DEMO_PASSWORD") or os.getenv("DEMO_PASS")
    )
    if pwd:
        return pwd
    generated = secrets.token_urlsafe(16)
    print(
        f"  {YELLOW}generated demo password: {generated} (set WILDFRAME_DEMO_PASSWORD to reuse){END}"
    )
    return generated


def _is_disposable_db_url(url: str | None) -> bool:
    if not url:
        return True
    low = url.lower()
    if "localhost" in low or "127.0.0.1" in low or "::1" in low:
        return True
    cleaned = url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    try:
        parsed = urlparse(cleaned)
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "::1", "postgres"):
            return True
        if any(x in host for x in ("prod", "rds", "amazonaws", "aurora")):
            return False
        if host and host not in ("postgres",):
            return False
        return True
    except Exception:
        return False


def assert_seed_allowed() -> None:
    env = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").strip().lower()
    override = os.getenv("DEV_SEED_ALLOWED", "").strip().lower() in ("1", "true", "yes")
    if env == "production" and not override:
        print(
            "refusing to seed: ENVIRONMENT=production (set DEV_SEED_ALLOWED=true to override explicitly)",
            file=sys.stderr,
        )
        sys.exit(1)
    db_url = os.getenv("DATABASE_URL")
    if db_url and not _is_disposable_db_url(db_url) and not override:
        print(
            "refusing to seed: DATABASE_URL looks non-disposable; set DEV_SEED_ALLOWED=true to override",
            file=sys.stderr,
        )
        sys.exit(1)


def ok(msg: str) -> None:
    print(f"  {GREEN}✔{END} {msg}")


def warn(msg: str) -> None:
    print(f"  {RED}!{END} {msg}")


DOCTYPES = [
    "Action",
    "Comedy",
    "Drama",
    "Sci-Fi",
    "Thriller",
    "Animation",
    "Documentary",
    "Fantasy",
]

MOVIES = [
    (
        "The Last Signal",
        "action",
        "A deep-space relay officer intercepts a message that should not exist.",
        129,
    ),
    (
        "Midnight Heist",
        "thriller",
        "A crew of thieves plans the perfect score on a train crossing the Alps.",
        111,
    ),
    (
        "Laugh Track",
        "comedy",
        "A washed-up sitcom star bets everything on a living-room stand-up tour.",
        104,
    ),
    (
        "Prism",
        "sci-fi",
        "A physicist discovers light can carry memories — and someone is listening.",
        138,
    ),
    (
        "The Long Winter",
        "drama",
        "Two sisters keep a mountain lodge alive through the hardest winter on record.",
        121,
    ),
    (
        "Dust & Roses",
        "drama",
        "A florist in a post-industrial port town rebuilds her family's shop.",
        97,
    ),
    (
        "Feral",
        "thriller",
        "A wildlife photographer records a pack of wolves — and they start recording back.",
        106,
    ),
    (
        "Solar Winds",
        "documentary",
        "Riding the storms of our sun with the engineers of the Parker probes.",
        89,
    ),
    (
        "The Cartographer",
        "fantasy",
        "A mapmaker discovers the world she draws changes the one she lives in.",
        133,
    ),
    (
        "Paper Planes",
        "animation",
        "A paper airplane takes a child on a journey across a giant's desk.",
        92,
    ),
]

SHOWS = [
    {
        "title": "Arc House",
        "slug": "arc-house",
        "desc": "Seven strangers share a haunted high-rise, and the house listens.",
        "genres": ["sci-fi", "thriller"],
        "seasons": [6, 5, 4],
    },
    {
        "title": "Blue Collar Kings",
        "slug": "blue-collar-kings",
        "desc": "A family builds a demolition empire one job at a time.",
        "genres": ["drama"],
        "seasons": [5, 5],
    },
    {
        "title": "Toast & Tonic",
        "slug": "toast-and-tonic",
        "desc": "Slice-of-life comedy following a late-night diner crew.",
        "genres": ["comedy"],
        "seasons": [4, 4, 4],
    },
]


def seed_subscription_and_moderation(user_id: str, token: str) -> None:
    import httpx as _hx

    gw = GATEWAY
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = _hx.post(
            f"{gw}/billing/api/v1/billing/subscribe/{user_id}",
            json={"tier": "svod"},
            headers=headers,
            verify=False,
            timeout=15,
        )
        print("subscribe:", r.status_code)
    except Exception as exc:  # noqa: BLE001
        print("subscribe skipped:", exc)
    try:
        r = _hx.post(
            f"{gw}/admin/api/v1/admin/users/moderate",
            json={"user_id": user_id, "status": "active", "reason": "seed"},
            headers={**headers, "X-Admin-Reauth": token},
            verify=False,
            timeout=15,
        )
        print("moderate:", r.status_code)
    except Exception as exc:  # noqa: BLE001
        print("moderate skipped:", exc)


def main() -> None:
    assert_seed_allowed()
    demo_email = get_demo_email()
    demo_password = get_demo_password()
    is_generated = not (
        os.getenv("WILDFRAME_DEMO_PASSWORD") or os.getenv("DEMO_PASSWORD") or os.getenv("DEMO_PASS")
    )
    print(f"{BOLD}Seeding Wildframe demo data for {demo_email}{END}")
    if is_generated:
        print(
            f"  {YELLOW}using generated password (set WILDFRAME_DEMO_PASSWORD to make it stable){END}"
        )

    with httpx.Client(timeout=30, verify=False) as client:
        register_user(client, demo_email, demo_password)
        token = login(client, demo_email, demo_password)
        if not token:
            warn("login failed — seeding halted")
            return
        user_id = DEMO_USER_ID
        try:
            r = client.get(f"{AUTH}/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                maybe = r.json().get("id")
                if maybe:
                    user_id = str(maybe)
        except Exception:
            pass
        client = auth_client(client, token)
        seed_subscription_and_moderation(user_id, token)

        genres = seed_genres(client)
        ok(f"{len(genres)} genres ready")

        g = {name.lower(): gd for name, gd in genres.items()}
        for title, slug, desc, dur in MOVIES:
            genre = [g[slug.lower()]]
            cid = create_content(client, title, slug, desc, "movie", genre, duration=dur)
            if cid:
                ok(f"movie {title}")

        for show in SHOWS:
            show_genres = [genres[x] for x in show["genres"]]
            cid = create_content(
                client, show["title"], show["slug"], show["desc"], "series", show_genres
            )
            if not cid:
                warn(f"could not create series {show['title']}")
                continue
            ok(f"series {show['title']}")
            existing_seasons: dict[int, str] = {}
            try:
                r = client.get(f"{CONTENT}/api/v1/content/{cid}/seasons")
                if r.status_code == 200:
                    existing_seasons = {s["season_number"]: s["id"] for s in r.json()}
            except Exception:
                pass
            for s_no, ep_count in enumerate(show["seasons"], start=1):
                if s_no in existing_seasons:
                    sid = existing_seasons[s_no]
                else:
                    rs = client.post(
                        f"{CONTENT}/api/v1/content/{cid}/seasons",
                        json={"season_number": s_no, "title": f"Season {s_no}"},
                    )
                    if rs.status_code not in (200, 201):
                        warn(f"season create failed: {rs.status_code}")
                        continue
                    sid = rs.json()["id"]
                    existing_seasons[s_no] = sid
                for e in range(1, ep_count + 1):
                    client.post(
                        f"{CONTENT}/api/v1/content/{cid}/seasons/{sid}/episodes",
                        json={
                            "episode_number": e,
                            "title": f"Season {s_no} Episode {e}",
                            "duration_minutes": 42 + (e % 13),
                            "thumbnail_url": f"https://picsum.photos/seed/{show['slug']}-{s_no}-{e}/320/180",
                        },
                    )
                ok(f"  Season {s_no} ({ep_count} episodes)")

        if is_generated:
            print(
                f"\n{BOLD}Done.{END}  Log in at https://localhost:3000/login with {demo_email} "
                f"(password printed above; set WILDFRAME_DEMO_PASSWORD to make it stable)"
            )
        else:
            print(
                f"\n{BOLD}Done.{END}  Log in at https://localhost:3000/login with {demo_email} "
                f"(password from WILDFRAME_DEMO_PASSWORD / DEMO_PASSWORD)"
            )


def seed_genres(client: httpx.Client) -> dict[str, dict]:
    def fetch() -> dict[str, dict]:
        out: dict[str, dict] = {}
        try:
            r = client.get(f"{CONTENT}/api/v1/genres")
            for gd in r.json():
                out[gd["slug"]] = gd
        except Exception:
            pass
        return out

    existing = fetch()
    for name in DOCTYPES:
        slug = name.lower().replace(" ", "-")
        if slug in existing:
            continue
        r = client.post(
            f"{CONTENT}/api/v1/genres",
            json={"name": name, "slug": slug, "description": f"{name} on Wildframe"},
        )
        if r.status_code in (200, 201):
            existing[slug] = r.json()
    return existing


def login(client: httpx.Client, email: str, password: str) -> str | None:
    r = client.post(
        f"{AUTH}/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    if r.status_code == 200:
        return r.json().get("access_token")
    warn(f"login failed: {r.status_code} {r.text[:120]}")
    return None


def auth_client(client: httpx.Client, token: str | None) -> httpx.Client:
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    return client


def register_user(client: httpx.Client, email: str, password: str) -> None:
    payload = {
        "email": email,
        "password": password,
        "first_name": "Demo",
        "last_name": "User",
    }
    r = client.post(f"{AUTH}/api/v1/auth/register", json=payload)
    if r.status_code in (200, 201):
        ok(f"created demo user {email}")
        return
    if r.status_code == 409 or (r.status_code == 400 and "exist" in r.text):
        ok("demo user already exists")
        return
    warn(f"could not create demo user: {r.status_code} {r.text[:160]}")


def create_content(
    client: httpx.Client,
    title: str,
    slug: str,
    desc: str,
    ctype: str,
    genres: list[dict],
    duration: int | None = None,
):
    payload = {
        "title": title,
        "slug": slug,
        "description": desc,
        "content_type": ctype,
        "original_language": "en",
        "poster_url": f"https://picsum.photos/seed/{slug}/300/450",
        "backdrop_url": f"https://picsum.photos/seed/{slug}/1280/720",
        "genre_ids": [g["id"] for g in genres],
    }
    if duration:
        payload["duration_minutes"] = duration
    r = client.post(f"{CONTENT}/api/v1/content", json=payload)
    if r.status_code in (200, 201):
        cid = r.json()["id"]
        pr = client.post(f"{CONTENT}/api/v1/content/{cid}/publish", json={"status": "published"})
        if pr.status_code not in (200, 201):
            warn(f"publish failed {pr.status_code}")
        return cid
    existing = find_by_slug(client, slug)
    if existing:
        return existing["id"]
    warn(f"content create failed {r.status_code} {r.text[:140]}")
    return None


def find_by_slug(client: httpx.Client, slug: str) -> dict | None:
    try:
        r = client.get(f"{CONTENT}/api/v1/content", params={"page": 1, "page_size": 100})
        for item in r.json():
            if item.get("slug") == slug:
                return item
    except Exception:
        pass
    return None


if __name__ == "__main__":
    main()
