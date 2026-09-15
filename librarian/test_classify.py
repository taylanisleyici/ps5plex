"""Run: python3 test_classify.py — asserts against names from a real library."""
from classify import classify

CASES = [
    # (release name, expected kind, expected title)
    ("La La Land (2016) (1080p BluRay x265 HEVC 10bit AAC 7.1 Tigole)", "movie", "La La Land"),
    ("Ted.2012.BluRay.1080p.x264.YIFY.mp4", "movie", "Ted"),
    # guessit alone calls this S1999E10 — the year-as-season guard must catch it
    ("10 Things I Hate About You (1999) (1080p BluRay x265 HEVC)", "movie",
     "10 Things I Hate About You"),
    # junk prefix must not end up in the title
    ("www.Torrenting.com - Whiplash 2014 UHD BluRay 1080p", "movie", "Whiplash"),
    ("The.Boys.S05E01.1080p.HEVC.x265-MeGusta.mkv", "series", "The Boys"),
    ("House.of.the.Dragon.S03E01.1080p.x265-ELiTE.mkv", "series", "House of the Dragon"),
    # Spanish "Cap.406" - no regex would find this
    ("Invencible [HDTV 1080p][Cap.406].mkv", "series", "Invencible"),
    # no season token at all, only the "Complete Collection" keyword
    ("[Anime Time] Attack On Titan (Complete Collection)", "series", None),
    ("Первый и последний. Сезон 1", "series", None),
]

OVERRIDES = {"shingeki no kyojin": "series", "frieren": "series"}

OVERRIDE_CASES = [
    # unresolvable from the name alone: no season, no episode, no keyword
    ("[Trix] Shingeki no Kyojin - AV1", "series"),
    ("Frieren꞉ Beyond Journey's End [BD][1080p][HEVC 10bit x265]", "series"),
]


def main():
    for name, want_kind, want_title in CASES:
        kind, info = classify(name)
        assert kind == want_kind, f"{name!r}: expected {want_kind}, got {kind}"
        if want_title is not None:
            assert info["title"] == want_title, \
                f"{name!r}: expected title {want_title!r}, got {info['title']!r}"

    for name, want_kind in OVERRIDE_CASES:
        kind, _ = classify(name, OVERRIDES)
        assert kind == want_kind, f"{name!r} with override: got {kind}"
        # and without the override it is genuinely undecidable — that's why it exists
        assert classify(name)[0] == "movie", f"{name!r} unexpectedly resolved without override"

    print(f"ok — {len(CASES) + len(OVERRIDE_CASES)} cases")


if __name__ == "__main__":
    main()
