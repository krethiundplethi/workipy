import unittest

from workipy.cli import build_parser, build_url


class CliTests(unittest.TestCase):
    def test_build_url_normalizes_slashes(self) -> None:
        self.assertEqual(
            build_url("https://api.clockify.me/api/v1/", "workspaces"),
            "https://api.clockify.me/api/v1/workspaces",
        )

    def test_parser_accepts_me_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--api-key", "demo", "me"])
        self.assertEqual(args.command, "me")


if __name__ == "__main__":
    unittest.main()
