import importlib.util
import pathlib

# Load models dynamically
module_path = pathlib.Path(__file__).resolve().parents[1] / "app" / "models" / "__init__.py"
spec = importlib.util.spec_from_file_location("content_models", module_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Load rights separately
rights_path = pathlib.Path(__file__).resolve().parents[1] / "app" / "models" / "rights.py"
spec_r = importlib.util.spec_from_file_location("content_rights", rights_path)
mod_r = importlib.util.module_from_spec(spec_r)
spec_r.loader.exec_module(mod_r)

Base = mod.Base
Content = mod.Content
ContentType = mod.ContentType
ContentStatus = mod.ContentStatus
Genre = mod.Genre
Season = mod.Season
Episode = mod.Episode
RightsHolder = mod_r.RightsHolder
TerritorialLicense = mod_r.TerritorialLicense


def test_content_genre_relationship():
    genre = Genre(name="Action", slug="action")
    content = Content(
        title="Test Movie",
        slug="test-movie",
        description="A test movie",
        content_type=ContentType.MOVIE,
        status=ContentStatus.DRAFT,
    )
    content.genres.append(genre)
    assert content.genres[0].name == "Action"


def test_season_and_episode_hierarchy():
    content = Content(
        title="Series X",
        slug="series-x",
        description="Series description",
        content_type=ContentType.SERIES,
        status=ContentStatus.DRAFT,
    )
    season = Season(content=content, season_number=1, title="Season 1")
    episode = Episode(
        content=content,
        season=season,
        episode_number=1,
        title="Pilot",
        duration_minutes=45,
    )
    assert episode.season.season_number == 1
    assert episode.content.title == "Series X"


def test_rights_holder_and_license():
    holder = RightsHolder(name="Studio A", type="studio")
    license = TerritorialLicense(
        content_id=holder.id,
        rights_holder_id=holder.id,
        territory="US",
        exclusive=True,
        avail_start="2023-01-01T00:00:00+00:00",
        avail_end="2024-01-01T00:00:00+00:00",
    )
    assert license.rights_holder_id == holder.id


def test_review_model():
    import importlib.util
    import pathlib
    import uuid

    rev_path = pathlib.Path(__file__).resolve().parents[1] / "app" / "models" / "reviews.py"
    spec = importlib.util.spec_from_file_location("content_review", rev_path)
    rev_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rev_mod)
    Review = rev_mod.Review
    review = Review(
        id=uuid.uuid4(),
        content_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        rating=5,
        text="Excellent",
        verified_viewer=False,
    )
    assert review.rating == 5


if __name__ == "__main__":
    test_content_genre_relationship()
    test_season_and_episode_hierarchy()
    test_rights_holder_and_license()
    print("All model tests passed.")
