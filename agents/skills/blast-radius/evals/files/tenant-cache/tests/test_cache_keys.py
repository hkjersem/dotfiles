import unittest

from src.session_cache import get_profile


class SessionCacheTests(unittest.TestCase):
    def test_profiles_are_isolated_for_simple_identifiers(self):
        cache = {}
        cache["tenant-a:profile"] = "A"
        cache["tenant-b:profile"] = "B"

        self.assertEqual(get_profile(cache, "tenant-a", "profile"), "A")
        self.assertEqual(get_profile(cache, "tenant-b", "profile"), "B")


if __name__ == "__main__":
    unittest.main()
