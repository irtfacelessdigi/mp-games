#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pembuat suara untuk game Kode Kecil (Google AI Studio / Gemini TTS)

Cara pakai singkat:
  1. pip install -U google-genai          (perlu versi 2.25.0 ke atas)
  2. Ambil API key di https://aistudio.google.com/apikey
       Mac/Linux : export GEMINI_API_KEY="kunci-kamu"
       Windows   : setx GEMINI_API_KEY "kunci-kamu"   (lalu buka terminal baru)
  3. (opsional) Dengar contoh beberapa suara dulu : python buat_suara.py --audisi
  4. Buat semua suara                              : python buat_suara.py --voice Leda
  5. Taruh kode-kecil.html bersama folder audio/ di tempat yang sama.

Aman dijalankan berulang: file yang sudah jadi dilewati, jadi kalau kena batas
kuota tinggal lanjutkan lagi nanti.
"""
import argparse, base64, csv, json, math, os, shutil, struct, subprocess, sys, time, wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSV_FILE = HERE / "kalimat-suara.csv"
AUDIO = HERE / "audio"
MODEL_DEFAULT = "gemini-3.8-flash-lite-tts"   # hemat, cocok untuk banyak klip pendek
STYLE_DEFAULT = "warm, cheerful, speaking slowly and clearly to young children"
AUDISI_VOICES = ["Leda", "Achird", "Sulafat", "Zephyr", "Aoede", "Puck", "Kore",
                 "Vindemiatrix", "Callirrhoe", "Sadachbia"]
AUDISI_TEXT = "Halo, aku Kiko! Ayo kita main coding bareng! Hebat, kamu pintar sekali!"
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def baca_baris():
    with open(CSV_FILE, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def sintesis(client, model, voice, style, teks):
    """Satu permintaan ke Gemini TTS. Mengembalikan byte WAV."""
    item = {"type": "text", "text": teks}
    if style:
        item["annotations"] = [{"type": "speech_metadata", "style": style}]
    it = client.interactions.create(
        model=model,
        input=[{"type": "user_input", "content": [item]}],
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": voice}]},
    )
    return base64.b64decode(it.output_audio.data)


def nada_palsu(teks):
    """Mode --fake: bunyi 'tut' pendek (untuk menguji game tanpa memakai kuota)."""
    dur = 0.25 + 0.07 * len(teks.split())
    sr = 24000
    frek = 330 + (sum(map(ord, teks)) % 400)
    n = int(sr * dur)
    pcm = bytearray()
    pcm += b"\x00\x00" * int(sr * 0.15)                    # hening awal (akan dipotong ffmpeg)
    for i in range(n):
        env = min(1, i / 400, (n - i) / 400)
        pcm += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * frek * i / sr)))
    pcm += b"\x00\x00" * int(sr * 0.15)                    # hening akhir
    tmp = HERE / "_tmp_fake.wav"
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(bytes(pcm))
    data = tmp.read_bytes(); tmp.unlink()
    return data


def simpan(wav_bytes, dasar):
    """Simpan klip. Pakai ffmpeg (kalau ada) untuk mengecilkan jadi mp3 dan memotong hening."""
    if HAS_FFMPEG:
        tmp = dasar.with_suffix(".tmp.wav")
        tmp.write_bytes(wav_bytes)
        out = dasar.with_suffix(".mp3")
        potong = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse,"
                  "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse")
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-af", potong,
                            "-ac", "1", "-ar", "24000", "-b:a", "64k", str(out)], check=True)
        finally:
            tmp.unlink(missing_ok=True)
        return out
    out = dasar.with_suffix(".wav")
    out.write_bytes(wav_bytes)
    return out


def sudah_ada(dasar):
    for ext in (".mp3", ".wav"):
        p = dasar.with_suffix(ext)
        if p.exists() and p.stat().st_size > 200:
            return p
    return None


def tulis_manifest(baris):
    clips = {}
    for r in baris:
        p = sudah_ada(AUDIO / r["file"])
        if p:
            clips[r["key"]] = p.name
    AUDIO.mkdir(exist_ok=True)
    (AUDIO / "manifest.js").write_text(
        "window.AUDIO_MANIFEST=" + json.dumps({"base": "audio/", "clips": clips}, ensure_ascii=False) + ";",
        encoding="utf-8")
    print(f"\nmanifest.js diperbarui: {len(clips)} dari {len(baris)} klip siap dipakai game.")


def buat_klien():
    try:
        from google import genai
    except ImportError:
        sys.exit("Belum terpasang. Jalankan dulu:  pip install -U google-genai")
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        sys.exit("API key belum diisi. Isi dulu GEMINI_API_KEY (lihat petunjuk di bagian atas file ini).")
    return genai.Client()


def panggil_dengan_ulang(fn, maks=5):
    for percobaan in range(1, maks + 1):
        try:
            return fn()
        except Exception as e:                       # noqa
            pesan = str(e)
            kecil = pesan.lower()
            if ("429" in pesan or "resource_exhausted" in kecil or "quota" in kecil):
                if "perday" in kecil.replace("_", "").replace(" ", "") or "per day" in kecil:
                    raise RuntimeError("KUOTA_HARIAN")
                tunggu = 25 * percobaan
                print(f"   kena batas permintaan, tunggu {tunggu} detik lalu coba lagi...")
                time.sleep(tunggu)
                continue
            if percobaan == maks:
                raise
            print(f"   gagal ({pesan[:120]}), coba lagi...")
            time.sleep(5 * percobaan)
    raise RuntimeError("gagal setelah beberapa kali mencoba")


def main():
    ap = argparse.ArgumentParser(description="Buat suara game Kode Kecil dengan Gemini TTS")
    ap.add_argument("--voice", default="Leda",
                    help="nama suara (mis. Leda, Achird, Sulafat) atau ID suara kustom 'voice_...' dari AI Studio")
    ap.add_argument("--model", default=MODEL_DEFAULT, help=f"model TTS (bawaan: {MODEL_DEFAULT})")
    ap.add_argument("--style", default=STYLE_DEFAULT, help="gaya bicara singkat; kosongkan dengan --style ''")
    ap.add_argument("--jeda", type=float, default=1.0, help="detik jeda antar permintaan (bawaan 1.0)")
    ap.add_argument("--batas", type=int, default=0, help="maksimal klip baru yang dibuat kali ini (0 = semua)")
    ap.add_argument("--ulang", action="store_true", help="buat ulang semua klip, termasuk yang sudah ada")
    ap.add_argument("--audisi", action="store_true", help="buat contoh beberapa suara di folder audisi/")
    ap.add_argument("--manifest-saja", action="store_true", help="hanya perbarui manifest.js")
    ap.add_argument("--fake", action="store_true", help="uji coba: bunyi 'tut' palsu tanpa memakai API")
    a = ap.parse_args()

    baris = baca_baris()
    AUDIO.mkdir(exist_ok=True)

    if a.manifest_saja:
        tulis_manifest(baris); return

    if a.audisi:
        client = buat_klien()
        out = HERE / "audisi"; out.mkdir(exist_ok=True)
        for v in AUDISI_VOICES:
            print("membuat contoh suara", v)
            wav = panggil_dengan_ulang(lambda: sintesis(client, a.model, v, a.style, AUDISI_TEXT))
            (out / f"{v}.wav").write_bytes(wav)
            time.sleep(a.jeda)
        print("\nSelesai. Dengarkan file di folder audisi/, lalu jalankan:  python buat_suara.py --voice NamaSuara")
        return

    client = None if a.fake else buat_klien()
    if not HAS_FFMPEG:
        print("Catatan: ffmpeg tidak ditemukan, klip disimpan sebagai .wav (lebih besar, hening tidak dipotong).")
    print(f"Suara: {a.voice} | Model: {a.model} | Gaya: {a.style or '(tanpa gaya)'}")

    dibuat = 0
    try:
        for n, r in enumerate(baris, 1):
            dasar = AUDIO / r["file"]
            if not a.ulang and sudah_ada(dasar):
                continue
            if a.batas and dibuat >= a.batas:
                print(f"Berhenti di batas {a.batas} klip.")
                break
            print(f"[{n}/{len(baris)}] {r['say']}")
            if a.fake:
                wav = nada_palsu(r["say"])
            else:
                wav = panggil_dengan_ulang(lambda: sintesis(client, a.model, a.voice, a.style, r["say"]))
            simpan(wav, dasar)
            dibuat += 1
            if not a.fake:
                time.sleep(a.jeda)
    except RuntimeError as e:
        if str(e) == "KUOTA_HARIAN":
            print("\nKuota harian habis. Tidak apa-apa: jalankan perintah yang sama lagi besok,"
                  " klip yang sudah jadi akan dilewati.")
        else:
            print("\nBerhenti:", e)
    except KeyboardInterrupt:
        print("\nDihentikan. Klip yang sudah jadi tetap tersimpan.")
    finally:
        tulis_manifest(baris)
    print(f"Klip baru kali ini: {dibuat}")


if __name__ == "__main__":
    main()
