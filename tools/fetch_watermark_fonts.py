#!/usr/bin/env python3
"""Developer tool (NOT run on a customer VPS): downloads the watermark font catalogue as real static .ttf files.

The encoder (FFmpeg drawtext, scripts/lib.sh build_watermark_filter) can only draw a font that exists as a file on the
server. This fetches the Regular (400) static TrueType file of every family in the catalogue from Google Fonts, checks
each one really is a loadable TrueType/OpenType font covering the script it is listed for, and writes:

    scripts/fonts/<key>.ttf          the font, unmodified, exactly as Google distributes it
    scripts/fonts/fonts.json         the manifest the dashboard and the tests read (family, key, category, scripts, sha256)
    scripts/fonts/FONT-LICENSES.md   per-font copyright + licence (from each font's own name table)

Usage:  python tools/fetch_watermark_fonts.py            (needs: pip install fonttools; network access)
A family that cannot be fetched or fails a check is reported and left OUT of the manifest - the picker only ever lists
fonts the encoder can actually render.
"""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

from fontTools.ttLib import TTFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "scripts" / "fonts"
# A non-browser user agent makes the CSS API answer with a plain TrueType URL (browsers get woff2 or EOT, which FreeType/FFmpeg
# builds do not reliably load).
UA = "Wget/1.21"

CATALOGUE = {
    "High-Impact & Broadcast Titles": [
        "Anton", "Bebas Neue", "Teko", "Archivo Black", "Russo One", "Righteous", "Bungee", "Alfa Slab One",
        "Black Han Sans", "Titan One", "Bangers", "Squada One",
    ],
    "Modern & Clean Sans-Serif": [
        "Montserrat", "Poppins", "Roboto", "Inter", "Open Sans", "Lato", "Raleway", "DM Sans", "Plus Jakarta Sans",
        "Work Sans", "Outfit", "Rubik", "Nunito", "Kanit",
    ],
    "Condensed & Tall": [
        "Oswald", "Barlow Semi Condensed", "Fjalla One", "Pathway Gothic One", "Saira Condensed", "Antonio", "Six Caps",
        "Yanone Kaffeesatz",
    ],
    "Tech, Sci-Fi & Gaming": [
        "Orbitron", "Exo 2", "Audiowide", "Rajdhani", "Chakra Petch", "Michroma", "Oxanium", "Share Tech Mono",
        "Press Start 2P", "Silkscreen", "JetBrains Mono",
    ],
    "Elegant & Editorial Serif": [
        "Playfair Display", "Merriweather", "Cinzel", "Lora", "Cormorant Garamond", "Bodoni Moda", "Spectral", "Prata",
        "DM Serif Display", "Abril Fatface",
    ],
    "Handwritten & Script": [
        "Pacifico", "Caveat", "Dancing Script", "Permanent Marker", "Great Vibes", "Satisfy", "Shadows Into Light", "Kalam",
    ],
    "Sri Lankan / Sinhala Unicode Support": ["Noto Sans Sinhala", "Noto Serif Sinhala", "Abhaya Libre"],
}
SINHALA = "Sri Lankan / Sinhala Unicode Support"


def slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


# Fonts whose own name table has no licence statement: the licence as confirmed from the Google Fonts repository.
LICENCE_OVERRIDES = {
    "Six Caps": ("SIL OFL 1.1 - confirmed 2026-11-11 from google/fonts ofl/sixcaps (OFL.txt, METADATA.pb); "
                 "the font file has no licence statement",
                 "https://github.com/google/fonts/tree/main/ofl/sixcaps"),
}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch(family: str) -> bytes:
    css = get("https://fonts.googleapis.com/css2?family=" + urllib.parse.quote_plus(family) + ":wght@400").decode()
    m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", css)
    if not m:
        raise RuntimeError("no font URL in the CSS answer")
    return get(m.group(1))


def name(font: TTFont, nid: int) -> str:
    rec = font["name"].getDebugName(nid)
    return (rec or "").strip()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest, licences, failed = [], [], []
    for category, families in CATALOGUE.items():
        for family in families:
            key = slug(family)
            try:
                try:
                    data = fetch(family)
                except Exception:
                    data = get("https://fonts.googleapis.com/css2?family=" + urllib.parse.quote_plus(family)).decode()
                    data = get(re.search(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", data).group(1))
                if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
                    raise RuntimeError(f"not a TrueType/OpenType file (magic {data[:4]!r})")
                font = TTFont(io.BytesIO(data))
                cmap = font.getBestCmap() or {}
                scripts = [s for s, cp in (("latin", 0x41), ("sinhala", 0x0D85)) if cp in cmap]
                if "latin" not in scripts:
                    raise RuntimeError("no Latin letters")
                if category == SINHALA and "sinhala" not in scripts:
                    raise RuntimeError("listed as Sinhala but has no Sinhala letters")
                if "fvar" in font:
                    raise RuntimeError("variable font - the encoder cannot pick a weight; a static file is required")
                (OUT / f"{key}.ttf").write_bytes(data)
                manifest.append({"key": key, "family": family, "file": f"{key}.ttf", "category": category,
                                 "scripts": scripts, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                licences.append((family, key, name(font, 0), name(font, 13), name(font, 14)))
                print(f"ok    {family:28s} {len(data) / 1024:7.0f} KB  {'+'.join(scripts)}")
            except Exception as exc:                                    # noqa: BLE001 - report every failure, keep going
                failed.append((family, str(exc)))
                print(f"FAIL  {family:28s} {exc}")
    (OUT / "fonts.json").write_text(json.dumps({"version": 1, "fonts": manifest}, indent=1) + "\n", encoding="utf-8",
                                    newline="\n")
    lines = ["# Watermark fonts - licences", "",
             "Every file in this directory is an unmodified font as distributed by Google Fonts. The copyright and licence",
             "statements below are copied from each font's own name table. Full licence texts: SIL Open Font License 1.1",
             "(https://openfontlicense.org), Apache License 2.0 (https://www.apache.org/licenses/LICENSE-2.0),",
             "Ubuntu Font Licence 1.0 (https://ubuntu.com/legal/font-licence). The full SIL OFL 1.1 and Apache 2.0 texts ship next to the fonts as OFL-1.1.txt and APACHE-2.0.txt, as both licences require.", "",
             "| Font | File | Copyright | Licence |", "|---|---|---|---|"]
    for family, key, copyright_, lic, url in licences:
        lic, url = LICENCE_OVERRIDES.get(family, (lic, url))
        lic_short = (lic.split(". ")[0][:160] + (" " + url if url else "")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {family} | {key}.ttf | {copyright_.replace('|', '/').replace(chr(10), ' ')[:200]} | {lic_short} |")
    (OUT / "FONT-LICENSES.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"\n{len(manifest)} fonts written to {OUT}; {len(failed)} failed")
    for family, why in failed:
        print(f"  - {family}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
