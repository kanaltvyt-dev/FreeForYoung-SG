#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources_sg.txt"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

UA = "FreeForYoung-SG/FINAL"
FETCH_TIMEOUT = 15
TCP_TIMEOUT = 3.0
TCP_ATTEMPTS = 2
WORKERS = 80
MAX_PER_SOURCE = 1500
MAX_TOTAL = 5000
MAX_PUBLISHED = 200
MAX_PER_IP = 2

# CDN/edge providers are excluded because endpoint GeoIP is not proof of
# physical server location.
EXCLUDED_WORDS = (
    "cloudflare", "fastly", "akamai", "cloudfront", "vercel",
    "netlify", "gcore", "bunny", "stackpath", "imperva",
    "incapsula", "cdn77", "edgecast", "limelight"
)

URI_RE = re.compile(r"(?:vless|vmess|trojan|ss)://[^\s<>'"`]+", re.I)


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return r.read().decode("utf-8", "ignore")


def b64decode_maybe(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 24 or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
        return None
    try:
        raw = base64.urlsafe_b64decode(compact + "=" * (-len(compact) % 4))
        out = raw.decode("utf-8", "ignore")
        return out if "://" in out else None
    except Exception:
        return None


def extract(text: str) -> list[str]:
    decoded = b64decode_maybe(text)
    if decoded:
        text = decoded

    result = []
    seen = set()
    for m in URI_RE.finditer(text):
        uri = m.group(0).rstrip("),;")
        if uri in seen:
            continue
        seen.add(uri)
        result.append(uri)
        if len(result) >= MAX_PER_SOURCE:
            break
    return result


def endpoint(uri: str):
    try:
        p = urlsplit(uri)
        if not p.hostname:
            return None
        return p.hostname, p.port or 443
    except Exception:
        return None


def resolve(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None


def geo_lookup(ips: list[str]) -> dict[str, dict]:
    result = {}
    for start in range(0, len(ips), 100):
        chunk = ips[start:start + 100]
        payload = json.dumps([
            {"query": ip, "fields": "query,status,countryCode,city,isp,org,as"}
            for ip in chunk
        ]).encode()
        req = Request(
            "http://ip-api.com/batch",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": UA},
            method="POST",
        )
        try:
            with urlopen(req, timeout=20) as r:
                rows = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print("GEO_FAIL:", e)
            continue

        for row in rows:
            if row.get("status") == "success" and row.get("query"):
                result[row["query"]] = row
    return result


def is_excluded_provider(info: dict) -> bool:
    hay = " ".join(
        str(info.get(k, "")) for k in ("isp", "org", "as")
    ).lower()
    return any(word in hay for word in EXCLUDED_WORDS)


def tcp_check(uri: str):
    ep = endpoint(uri)
    if not ep:
        return None, None

    host, port = ep
    ip = resolve(host)
    if not ip:
        return None, None

    samples = []
    for _ in range(TCP_ATTEMPTS):
        start = time.perf_counter()
        try:
            with socket.create_connection((ip, port), timeout=TCP_TIMEOUT):
                samples.append(round((time.perf_counter() - start) * 1000, 1))
        except Exception:
            pass

    if not samples:
        return None, ip
    samples.sort()
    return samples[len(samples) // 2], ip


def main():
    all_nodes = []
    source_stats = {}

    for line in SOURCES.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        try:
            nodes = extract(fetch(url))
            source_stats[url] = len(nodes)
            all_nodes.extend(nodes)
            print("SOURCE", len(nodes), url)
        except Exception as e:
            source_stats[url] = f"ERROR: {e}"
            print("SOURCE_FAIL", url, e)

    all_nodes = list(dict.fromkeys(all_nodes))[:MAX_TOTAL]

    # Resolve and GeoIP-filter by endpoint IP.
    resolved = {}
    for uri in all_nodes:
        ep = endpoint(uri)
        if not ep:
            continue
        ip = resolve(ep[0])
        if ip:
            resolved[uri] = ip

    geo = geo_lookup(list(dict.fromkeys(resolved.values())))

    sg_candidates = []
    for uri, ip in resolved.items():
        info = geo.get(ip)
        if not info:
            continue
        if str(info.get("countryCode", "")).upper() != "SG":
            continue
        if is_excluded_provider(info):
            continue
        sg_candidates.append((uri, ip, info))

    # TCP test only on SG candidates.
    alive = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(tcp_check, uri): (uri, ip, info)
                   for uri, ip, info in sg_candidates}
        for future in as_completed(futures):
            uri, ip, info = futures[future]
            try:
                ping, resolved_ip = future.result()
            except Exception:
                ping, resolved_ip = None, ip
            if ping is None:
                continue
            alive.append({
                "uri": uri,
                "ip": resolved_ip or ip,
                "ping_ms": ping,
                "city": info.get("city"),
                "isp": info.get("isp"),
                "org": info.get("org"),
                "as": info.get("as"),
            })

    alive.sort(key=lambda x: (x["ping_ms"], x["ip"]))

    published = []
    ip_counts = {}
    for item in alive:
        ip = item["ip"]
        if ip_counts.get(ip, 0) >= MAX_PER_IP:
            continue
        ip_counts[ip] = ip_counts.get(ip, 0) + 1
        published.append(item)
        if len(published) >= MAX_PUBLISHED:
            break

    uris = [x["uri"] for x in published]

    header = (
        "#profile-title: FreeForYoung SG Final\n"
        "#announce: SG GeoIP + CDN filtered + TCP checked\n"
        "#subscription-auto-update-enable: 1\n"
        "#subscription-ping-onopen-enabled: 1\n"
        "#subscriptions-sort-type: ping\n"
        "#ping-type: proxy\n"
        "#check-url-via-proxy: https://cp.cloudflare.com/generate_204\n"
        "#ping-result: time\n"
    )

    (OUT / "singapore.txt").write_text(
        header + "\n".join(uris) + ("\n" if uris else ""),
        encoding="utf-8",
    )

    (OUT / "singapore-base64.txt").write_text(
        base64.b64encode("\n".join(uris).encode()).decode() + "\n",
        encoding="utf-8",
    )

    stats = {
        "generated_at_utc": int(time.time()),
        "raw_unique": len(all_nodes),
        "resolved": len(resolved),
        "geoip_singapore_after_cdn_filter": len(sg_candidates),
        "tcp_alive": len(alive),
        "published": len(published),
        "source_stats": source_stats,
        "servers": published,
    }
    (OUT / "singapore-stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps({
        k: stats[k]
        for k in (
            "raw_unique",
            "resolved",
            "geoip_singapore_after_cdn_filter",
            "tcp_alive",
            "published",
        )
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
