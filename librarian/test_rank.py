"""Run: python3 test_rank.py"""
from rank import score, is_banned

# the actual file that started this: a camera recording with a betting-site overlay
CAM = "Project.Hail.Mary.2026.1080p.CAMRip.Dublado.Official.mkv"
GOOD = "Project.Hail.Mary.2026.1080p.BluRay.x264-SPARKS.mkv"


def main():
    assert is_banned(CAM), "CAMRip must be recognised as junk"
    assert score(CAM) is None, "a CAM must never be selectable"
    assert score(GOOD) is not None

    # a 1080p x264 must beat a 4K x265, because the PS5 can direct stream one
    # and has to software-transcode the other
    assert score("Movie.2024.1080p.BluRay.x264-GRP.mkv") > \
           score("Movie.2024.2160p.BluRay.x265-GRP.mkv")

    # MP4 + x264 is the best case of all: pure Direct Play
    assert score("Movie.2024.1080p.BluRay.x264-GRP.mp4") > \
           score("Movie.2024.1080p.BluRay.x264-GRP.mkv")

    # HDR costs a software tonemap, so plain SDR wins at equal resolution
    assert score("Movie.2024.1080p.BluRay.x265-GRP.mkv") > \
           score("Movie.2024.1080p.BluRay.HDR.DoVi.x265-GRP.mkv")

    # and a real 720p beats a fake 1080p camera rip
    assert score("Movie.2024.720p.WEB-DL.x264.mkv") is not None
    assert score("Movie.2024.1080p.HDCAM.mkv") is None

    print("ok — 7 assertions")


if __name__ == "__main__":
    main()
