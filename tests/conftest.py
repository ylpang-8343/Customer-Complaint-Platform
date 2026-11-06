import pytest
from django.core.management import call_command


@pytest.fixture(autouse=True)
def fast_password_hashing(settings):
    # The default hasher is deliberately slow, and every test seeds about 20 users.
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def temporary_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def seeded(db):
    call_command("seed", "--demo", verbosity=0)


@pytest.fixture
def user_of(seeded):
    from accounts.models import User

    return lambda username: User.objects.get(username=username)
