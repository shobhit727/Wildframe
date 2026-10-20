import pathlib
import sys

# The service root (services/admin-service), not services/: adding the latter
# puts the bare name `app` in scope for every service, so `app` could resolve to
# a stray package next to services/ instead of this service's application.
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
