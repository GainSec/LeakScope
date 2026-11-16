# Catalog artwork and references

Local SVGs identify catalog technologies without requests to logo CDNs or vendors.
`manifest.json` records every bundled asset's upstream source, pinned package,
SHA-256, license and available brand guidelines. Simple Icons is CC0-1.0 (full
text in `LICENSE-SIMPLE-ICONS.txt`); names and trademarks belong to their owners.
Older Simple Icons 11.15.0 provides marks absent from 16.0.0. Brand colors were
added as SVG fill attributes; geometry is unchanged. Four common project marks
are bundled from their official sites (Qdrant, Weaviate, QuestDB and Memcached);
no separate logo license was stated on those source assets, so their copyright
and trademark remain with the project owners. See their exact sources and
modifications in the manifest; no additional license grant is implied.

`protocol-*.svg` are neutral protocol/classification emblems, and `generic-*.svg`
are neutral fallbacks for products without a vetted logo. Both are original
LeakScope drawings released under CC0-1.0, not official vendor/standard logos.
The UI must identify these generic emblems honestly.

The fixed reference map is in `leakscope/phase3/catalog_presentation.py`. Links
open documentation or official distribution/installation landing pages. They do
not download or search automatically. Hosted services, protocol-only entries,
hardware and screenshot-only unidentified clues intentionally omit downloads.
Rockware Magic IMM and Status PW have no verified official project references;
their docs/homepage/download links remain empty. No live link checking happens
while listing or browsing the API.
