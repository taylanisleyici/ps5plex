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

# --- ordering downloaded files by what they contain -----------------------------
from picker import order_saved, speech_times
def srt(cues):
    return "\n\n".join(f"{i}\n00:{m:02d}:{s:02d},000 --> 00:{m:02d}:{s+1:02d},000\n{t}"
                       for i, (m, s, t) in enumerate(cues, 1)).encode()
official = srt([(4, 9 + 2*i, f"line {i}") for i in range(200)])              # starts 4:09
fuller = srt([(2, 49, "Sakin."), (2, 53, "Sakin.")] + [(4, 9 + 2*i, f"satır {i}") for i in range(200)])
forced = srt([(2, 49, "Sakin."), (2, 53, "Sakin.")])                          # Valyrian only
sdh = srt([(0, 6, "[dragon grunting]"), (2, 49, "[speaks High Valyrian]")] + [(4, 9 + 2*i, f"line {i}") for i in range(200)])
shifted = srt([(0, 1 + 2*i, f"line {i}") for i in range(200)])                # same lines, 4 min early
assert len(speech_times(sdh)) == 200                                          # brackets are not speech
# the House of the Dragon case: same timing, covers the burned-in Valyrian -> first; forced-only -> last
assert order_saved([(0, official), (1, fuller), (2, forced)]) == [1, 0, 2]
# SDH noise before the first line does not count as coverage
assert order_saved([(0, official), (1, sdh)]) == [0, 1]
# a file on a different timing is never promoted, however early it starts
assert order_saved([(0, official), (1, shifted)]) == [0, 1]

# --- encoding repair ---------------------------------------------------------

# the actual Hail Mary file: UTF-8 read as cp1252 and re-saved as UTF-8
assert clean_srt("KURTULUÅž PROJESÄ°".encode("utf-8")) == "KURTULUŞ PROJESİ".encode("utf-8")
assert clean_srt("GÃ¶z hareketi".encode("utf-8")) == "Göz hareketi".encode("utf-8")
# a C1 control (0x81) from a Windows misread must not block the whole file: "Á" is C3 81
assert clean_srt("Ã\x81lvaro dedi ki: GÃ¶z".encode("utf-8")) == "Álvaro dedi ki: Göz".encode("utf-8")
# already clean: untouched
assert clean_srt("İşte başlıyoruz.".encode("utf-8")) == "İşte başlıyoruz.".encode("utf-8")
# legacy code pages become UTF-8, chosen by the subtitle's language
assert clean_srt("Çalış".encode("cp1254"), "tur") == "Çalış".encode("utf-8")
assert clean_srt("Привет".encode("cp1251"), "rus") == "Привет".encode("utf-8")
assert clean_srt("Straße".encode("cp1252"), "ger") == "Straße".encode("utf-8")
# a BOM is dropped
assert clean_srt(b"\xef\xbb\xbfTamam") == b"Tamam"
print("ok — 19 assertions")
