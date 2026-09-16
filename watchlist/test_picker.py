"""Run inside the picker container: python3 test_picker.py"""
from picker import clean_srt

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
print("ok — 6 assertions")
