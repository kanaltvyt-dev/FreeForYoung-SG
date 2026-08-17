# FreeForYoung SG

Singapore-only public-node aggregator.

Pipeline:
1. Fetch multiple public feeds.
2. Extract VLESS / VMess / Trojan / Shadowsocks.
3. Resolve endpoint IPs.
4. Keep only GeoIP country `SG`.
5. Exclude common CDN/edge providers.
6. TCP-test every SG candidate.
7. Deduplicate and publish fastest nodes.
8. Rebuild hourly.

## Public outputs

- `output/singapore.txt` — Happ / URI subscription
- `output/singapore-base64.txt` — v2rayN/v2rayNG-compatible Base64
- `output/singapore-stats.json` — build diagnostics

This is a public aggregator, not a guarantee of physical server location or availability from every ISP.
