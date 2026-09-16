"""Run inside the picker container: python3 test_picker.py"""
from picker import clean_srt, pick_subtitles, sub_score

# --- which subtitle fits the picked release ---------------------------------
RELEASE = "Project.Hail.Mary.2026.iNTERNAL.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HDR10.x265-SPx.mkv"
TELESYNC = {"id": "1", "lang": "tur", "movieReleaseName": "Project Hail Mary.2026.1080p.TELESYNC.HC.x264-SyncUP"}
WEBDL = {"id": "2", "lang": "tur", "movieReleaseName": "Project.Hail.Mary.2026.1080p.WEB-DL.DDP5.1.H264-Esub"}
BLURAY = {"id": "3", "lang": "tur", "movieReleaseName": "Project.Hail.Mary.2026.1080p.BluRay.x264-SPARKS"}
HASHED = {"id": "4", "lang": "tur", "m": "h", "movieReleaseName": "whatever"}
FORCED = {"id": "5", "lang": "tur", "movieReleaseName": "Project.Hail.Mary.2026.1080p.BluRay.x264-Forced"}
# the actual bug: the telesync-timed file was first in the list and got saved
assert sub_score(TELESYNC, RELEASE) < sub_score(WEBDL, RELEASE) < sub_score(BLURAY, RELEASE)
assert sub_score(HASHED, RELEASE) > sub_score(BLURAY, RELEASE)
assert sub_score(FORCED, RELEASE) < sub_score(WEBDL, RELEASE)
picked = pick_subtitles([TELESYNC, WEBDL, BLURAY, HASHED, FORCED, dict(BLURAY, lang="eng")], RELEASE, "tur", n=3)
assert [s["id"] for s in picked] == ["4", "3", "2"], [s["id"] for s in picked]

# --- which file in a season pack is which episode -----------------------------
from picker import episodes_in
assert episodes_in("The.Pitt.S01E07.1080p.WEB.h264-ETHEL.mkv") == {7}
assert episodes_in("Show.S02E01-E02.1080p.mkv") == {1, 2}
assert episodes_in("Show.2024.1080p.BluRay.x264.mkv") == set()

# --- encoding repair ---------------------------------------------------------

# the actual Hail Mary file: UTF-8 read as cp1252 and re-saved as UTF-8
assert clean_srt("KURTULUÅž PROJESÄ°".encode("utf-8")) == "KURTULUŞ PROJESİ".encode("utf-8")
assert clean_srt("GÃ¶z hareketi".encode("utf-8")) == "Göz hareketi".encode("utf-8")
# a C1 control (0x81) from a Windows misread must not block the whole file: "Á" is C3 81
assert clean_srt("Ã\x81lvaro dedi ki: GÃ¶z".encode("utf-8")) == "Álvaro dedi ki: Göz".encode("utf-8")
# already clean: untouched
assert clean_srt("İşte başlıyoruz.".encode("utf-8")) == "İşte başlıyoruz.".encode("utf-8")
# legacy Windows-1254 becomes UTF-8
assert clean_srt("Çalış".encode("cp1254")) == "Çalış".encode("utf-8")
# a BOM is dropped
assert clean_srt(b"\xef\xbb\xbfTamam") == b"Tamam"
print("ok — 13 assertions")
