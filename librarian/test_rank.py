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

    # a Chinese hard-subbed rip must not beat the plain English release
    assert score("The.Pitt.S01E01.1080p.ENG.ITA.H264-TheBlackKing.mkv", origin="en") > \
           score("匹兹堡医护前线.2025.S01E01.HD1080P.AAC.H264.CHS-ENG.mkv", origin="en")

    # a Spanish dub of an American film loses to the plain release
    assert score("Movie.2024.1080p.BluRay.x264-GRP.mkv", origin="en") > \
           score("Movie.2024.1080p.BluRay.x264.Castellano.mkv", origin="en")

    # but for a Spanish film, Castellano IS the original audio — no penalty
    assert score("Pelicula.2024.1080p.BluRay.x264.Castellano.mkv", origin="es") == \
           score("Pelicula.2024.1080p.BluRay.x264.mkv", origin="es")

    # and for a Turkish series a Turkish track is the original, not a dub
    assert score("Dizi.2024.1080p.WEB-DL.x264.Turkce.Dublaj.mkv", origin="tr") > \
           score("Dizi.2024.1080p.WEB-DL.x264.Turkce.Dublaj.mkv", origin="en")

    # subtitles are not dubs: VOSTFR keeps the original Japanese audio
    assert score("Anime.S01E01.1080p.BluRay.x264.VOSTFR.mkv", origin="ja") > \
           score("Anime.S01E01.1080p.BluRay.x264.TRUEFRENCH.mkv", origin="ja")

    # dual audio keeps the original in there somewhere, so only a light penalty
    assert score("Anime.S01E01.1080p.BluRay.x264.Dual.Audio.ITA.mkv", origin="ja") > \
           score("Anime.S01E01.1080p.BluRay.x264.ITA.mkv", origin="ja")

    # a plainly-named file inside a Tamil-dubbed torrent: only the surrounding
    # torrent name gives it away, so the context must be scored too
    inner = "The.Pitt.S01E07.1080p.mkv"
    assert score(inner, 0, "en") > score(
        inner, 0, "en", context="www.1tamilblasters.cool - The Pitt (2025) S01 EP(07) Tamil")
    # and a CAM torrent holding a plainly-named file is still a CAM
    assert score(inner, 0, "en", context="Some.Movie.2026.HDCAM.x264") is None

    # a five-dub aggregator repack must lose to a clean English release, even
    # though it does technically include an English track
    assert score("The.Pitt.S01E07.1080p.ENG.ITA.H264-TheBlackKing.mkv", 1_200_000_000, "en") > \
           score("www.1tamilblasters.cool - The Pitt (2025) S01 EP(07) "
                 "[1080p HD AVC - x264 - [Tam + Tel + Kan + Hin + Eng]", 1_390_000_000, "en")

    from rank import origin_language
    assert origin_language("Japan") == "ja"
    assert origin_language("United States, Hong Kong") == "en"
    assert origin_language(None) is None

    # on a ~45 Mbps link, a 30 GB remux of a 2h film (~33 Mbps) must lose to an
    # 8 GB encode (~9 Mbps) of the same quality tier
    from rank import est_mbps
    assert round(est_mbps(30 * 1024**3, 120)) == 36
    assert score("Film.2024.1080p.BluRay.x264-GRP.mkv", 8 * 1024**3, "en", runtime_min=120) > \
           score("Film.2024.1080p.BluRay.x264-GRP.mkv", 30 * 1024**3, "en", runtime_min=120)
    # without a runtime nothing changes, so the librarian's calls keep working
    assert score("Film.2024.1080p.BluRay.x264-GRP.mkv", 30 * 1024**3, "en") is not None

    # a 100 MB "1080p" file for a 2h film is a sample, not a release
    assert score("Film.2024.1080p.x264-GRP.mkv", 100 * 1024**2, "en", runtime_min=120) < \
           score("Film.2024.720p.x264-GRP.mkv", 3 * 1024**3, "en", runtime_min=120)

    print("ok — 23 assertions")


if __name__ == "__main__":
    main()
