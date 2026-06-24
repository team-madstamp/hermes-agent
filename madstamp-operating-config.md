# Hermes Agent — Operating Config (Madstamp / 강유)

> Drop this into the NAS Hermes agent's memory or AGENTS instructions
> (e.g. `~/.hermes` memory, or `AGENTS.md` in `MadKangYu/hermes-agent`).
> Generated 2026-06-24.

## Host / network

- **Run host:** NAS `madstamp-nas` (Synology, Linux). This is the 24/7 Hermes host. Everything below runs here, not on a laptop.
- **Tailscale:** tailnet `tail8d58b9.ts.net` under `richardowen7212@gmail.com`, MagicDNS + HTTPS on.
  - NAS: `madstamp-nas.tail8d58b9.ts.net` · Tailscale IP `100.78.161.58` · LAN `172.30.1.49`
  - Other devices: `m5-macbookpro` `100.91.113.98`, `max-proui-macbookpro` `100.71.226.39`
- **Hermes dashboard:** port `9119` → `http://madstamp-nas.tail8d58b9.ts.net:9119/` (Kanban tab at `/kanban`).
- **Honcho memory:** `app.honcho.dev` (cloud) / local Docker `localhost:8000`.

## Operating directive (run on the NAS)

- **SmartStore operations** (smartstore.naver.com/mad_stamp, login id `madstamp1`) run on the NAS Hermes.
- **`order@madstamp.co.kr` Madstamp 주문제작(custom-order) email** intake and processing run on the NAS Hermes.
  - order@ routes into team@madstamp.co.kr (Workspace uid=0).
  - Batch cutoffs 09:00 / 12:00 / 16:00; produce per-channel (스마트스토어/소셜) xlsx + 합본/송장출력용/CX팀확인용, plus 넥스트엔진 드롭 파일 and 우체국 계약고객 업로드 파일.
  - Naming: `{MM.DD} 도장 {차수}차 {채널} {차수}차[+소셜]({start}-{end}).xlsx`.
  - Automation target: replace VBA macros with openpyxl scripts.

## Bookmarked apps (logo-only style, mirror of Chrome bookmark bar)

| App | URL |
|---|---|
| Gmail | https://mail.google.com/ |
| ChatGPT | https://chatgpt.com/ |
| GitHub | https://github.com/ |
| X | https://x.com/ |
| Hermes dashboard | http://madstamp-nas.tail8d58b9.ts.net:9119/ |
| Kanban | http://madstamp-nas.tail8d58b9.ts.net:9119/kanban |
| Honcho | https://app.honcho.dev/ |

Management rule: keep these as a flat row, logo/favicon only (no text labels).
