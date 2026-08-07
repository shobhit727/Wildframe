"""Seed the Wildframe local stack with demo data so the UI has real content.

Hits each service directly on its host port (bypassing the gateway) for
reliability:

  auth-service      8001  -> /api/v1/auth/...
  content-service   8003  -> /api/v1/...

Creates: genres, a demo user, ~10 movies, 3 shows with seasons+episodes.
Posters/backdrops use picsum.photos placeholder images so the UI renders.
"""

import httpx

AUTH = "http://localhost:8001"
CONTENT = "http://localhost:8003"

DEMO_EMAIL = "demo@wildframe.com"
DEMO_PASSWORD = "DemoPass123!"

BOLD, RED, GREEN, YELLOW, END = "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}\u2714{END} {msg}")


def warn(msg: str) -> None:
    print(f"  {RED}!{END} {msg}")


DOCTYPES = ["Action", "Comedy", "Drama", "Sci-Fi", "Thriller", "Animation", "Documentary", "Fantasy"]

MOVIES = [
    ("The Last Signal", "action", "A deep-space relay officer intercepts a message that should not exist.", 129),
    ("Midnight Heist", "thriller", "A crew of thieves plans the perfect score on a train crossing the Alps.", 111),
    ("Laugh Track", "comedy", "A washed-up sitcom star bets everything on a living-room stand-up tour.", 104),
    ("Prism", "sci-fi", "A physicist discovers light can carry memories — and someone is listening.", 138),
    ("The Long Winter", "drama", "Two sisters keep a mountain lodge alive through the hardest winter on record.", 121),
    ("Dust & Roses", "drama", "A florist in a post-industrial port town rebuilds her family's shop.", 97),
    ("Feral", "thriller", "A wildlife photographer records a pack of wolves — and they start recording back.", 106),
    ("Solar Winds", "documentary", "Riding the storms of our sun with the engineers of the Parker probes.", 89),
    ("The Cartographer", "fantasy", "A mapmaker discovers the world she draws changes the one she lives in.", 133),
    ("Paper Planes", "animation", "A paper airplane takes a child on a journey across a giant's desk.", 92),
]

SHOWS = [
    {"title": "Arc House", "slug": "arc-house", "desc": "Seven strangers share a haunted high-rise, and the house listens.", "genres": ["sci-fi", "thriller"], "seasons": [6, 5, 4]},
    {"title": "Blue Collar Kings", "slug": "blue-collar-kings", "desc": "A family builds a demolition empire one job at a time.", "genres": ["drama"], "seasons": [5, 5]},
    {"title": "Toast & Tonic", "slug": "toast-and-tonic", "desc": "Slice-of-life comedy following a late-night diner crew.", "genres": ["comedy"], "seasons": [4, 4, 4]},
]


def main() -> None:
    print(f"{BOLD}Seeding Wildframe demo data{END}")

    with httpx.Client(timeout=30) as client:
        genres = seed_genres(client)
        ok(f"{len(genres)} genres ready")

        register_user(client)

        g = {name.lower(): gd for name, gd in genres.items()}
        for title, slug, desc, dur in MOVIES:
            genre = [g[slug.lower()]]
            cid = create_content(client, title, slug, desc, "movie", genre, duration=dur)
            if cid:
                ok(f"movie {title}")

        for show in SHOWS:
            show_genres = [genres[x] for x in show["genres"]]
            cid = create_content(client, show["title"], show["slug"], show["desc"], "series", show_genres)
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

        print(
            f"\n{BOLD}Done.{END}  Log in at http://localhost:3000/login with "
            f"{DEMO_EMAIL} / {DEMO_PASSWORD}"
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


def register_user(client: httpx.Client) -> None:
    payload = {
        "email": DEMO_EMAIL,
        "password": DEMO_PASSWORD,
        "first_name": "Demo",
        "last_name": "User",
    }
    r = client.post(f"{AUTH}/api/v1/auth/register", json=payload)
    if r.status_code in (200, 201):
        ok(f"created demo user {DEMO_EMAIL}")
        return
    if r.status_code == 409 or (r.status_code == 400 and "exist" in r.text):
        ok(f"demo user already exists")
        return
    warn(f"could not create demo user: {r.status_code} {r.text[:160]}")


def create_content(client: httpx.Client, title: str, slug: str, desc: str, ctype: str, genres: list[dict], duration: int | None = None):
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