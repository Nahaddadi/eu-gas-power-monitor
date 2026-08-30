# EU Gas / Power Desk Monitor

Daily cross-commodity monitor for European energy markets: public gas, carbon
and German power data in, desk metrics and charts out, plus an analyst desk
note drafted by a free LLM that can look things up on the web.

## ▶ Try it live

**[eu-gas-power-monitor.streamlit.app](https://eu-gas-power-monitor.streamlit.app)**

## What a run produces

Each run writes a dated folder under `runs/`: `metrics.json`, `snapshot.txt`,
three charts, a drafted `desk_note.txt`, and `research_log.json` listing every
search and page that fed the note.

Example snapshot (2026-08-23):

```text
EU gas storage   : 62%  (+13 pp vs period avg 49%)
TTF front        : EUR 66.0/MWh  (curve backwardation, -0.4 front->Dec)
EUA              : EUR 78.5/t  (20d momentum -0.7%)
DE power 7d avg  : EUR 143/MWh  (30d avg EUR 124)
Clean spark 7d   : EUR -20/MWh  (gas out of the money)
Gas SRMC         : EUR 163/MWh
```

A real drafted note: [docs/desk_note.txt](docs/desk_note.txt).

## Data

| Feed | Source |
|---|---|
| EU gas storage | GIE AGSI+ |
| TTF curve, EUA carbon | ICE contracts via Yahoo Finance |
| German day-ahead power | Energy-Charts (Fraunhofer ISE) |

The TTF contract months are generated from today's date, so the curve never
goes stale. Clean spark assumptions: CCGT efficiency 50%, emissions
0.37 tCO2/MWh, VOM EUR 2/MWh.

## The note writer

The note is drafted by Google's Gemini free tier (flash models via their
OpenAI-compatible API, with `-latest` aliases so catalog rotations do not
break anything). It never browses on its own: it asks for web searches or
specific pages within an allowlist of energy sources, the app fetches them
locally, then a final call writes the note, anchored on the metrics and
citing its sources inline. Custom source links can replace the built-in
allowlist from the app sidebar: a site root opens the whole site, a section
link that section and everything nested under it, an article link that page
only. Without API keys the monitor still produces metrics and charts.
