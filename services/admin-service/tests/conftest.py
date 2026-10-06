"""Register the admin-service integration fixtures with pytest."""

# Pytest otherwise does not discover the fixtures stored under app/tests.
pytest_plugins = ("app.tests.conftest",)
