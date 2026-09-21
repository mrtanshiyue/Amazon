#!/usr/bin/env python3
from __future__ import annotations

import html
import os
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, urldefrag
from urllib.request import Request, urlopen

README_URL = "https://raw.githubusercontent.com/ddgksf2013/ddgksf2013/main/README.md"
MIRROR_BASE = "https://raw.githubusercontent.com/ifflagged/Romeo/main/Modules/Surge/ddgksf2013/Official/"
TARGET = Path("modules/StartUpAds-Surge.sgmodule")
PUBLIC_RAW_URL = "https://raw.githubusercontent.com/mrtanshiyue/Amazon/main/modules/StartUpAds-Surge.sgmodule"
USER_AGENT = "mrtanshiyue-amazon-moyu-full-sync/5.0"

EXPECTED_COUNTS = {
    "会员解锁": 11,
    "广告屏蔽": 42,
    "应用增强": 13,
    "网页优化": 8,
}
CATEGORIES = list(EXPECTED_COUNTS)

# README file names that differ from the maintained Surge mirror.
MIRROR_ALIASES = {
    "BilibiliVip.conf": "BiliBiliAds.conf",
    "XmlyAdBlock.conf": "Ximalaya.conf",
    "Netease.conf": "NeteaseAds.conf",
    "smzdmAds.conf": "SmzdmAds.conf",
    "MailAds.conf": "NeteaseMailAds.conf",
    "CaiXinZhouKanProCrack.js": "CXZK.vip.js",
    "WeatherKit.snippet": "iRingo.WeatherKit.snippet",
    "Zhihu_Plus.conf": "Zhihu.Adblock.js",
    "Zhihu.Adblock.js": "zhihu.ads.js",
    "baidumap.conf": "bdmap.ads.js",
    "SuiShouJi.conf": "suishouji.ads.js",
    "PixivAds.js": "pixivAds.js",
    "CoolapkAds.js": "coolapk.js",
    "kuwomusic.vip.js": "kkmusic.vip.js",
    "Nicegram.pro.js": "nicegram.vip.js",
    "revenuecat.js": "revenuecat.vip.js",
    "buyitunes.js": "buyitunes.vip.js",
    "XiaoHongShu.conf": "XiaoHongShuAds.conf",
}

SOURCE_OVERRIDES = {
    "https://raw.githubusercontent.com/VirgilClyne/iRingo/main/snippet/Location.snippet":
        "https://github.com/NSRingo/GeoServices/releases/latest/download/Location.sgmodule",
}

DIRECTORY_ONLY_NAMES = {"更多应用去广告"}

FETCH_TIMEOUT = 35
MAX_FETCH_ATTEMPTS = 3


@dataclass
class Entry:
    category: str
    index: int
    name: str
    url: str | None
    display_file: str | None
    deprecated: bool = False
    source_used: str | None = None
    source_mode: str | None = None
    status: str = "PENDING"
    note: str = ""
    counts: Counter = field(default_factory=Counter)


def fetch_text(url: str, attempts: int = MAX_FETCH_ATTEMPTS, timeout: int = FETCH_TIMEOUT) -> str:
    clean_url, _ = urldefrag(url)
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(
                clean_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/plain,*/*;q=0.8",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                data = response.read()
                text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
                if not text.strip():
                    raise ValueError("empty response")
                return text
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"{clean_url}: {last_error}")


def looks_like_html(text: str) -> bool:
    head = text[:3000].lower()
    return (
        "<!doctype html" in head
        or "<html" in head
        or "<head>" in head
        or ("<body" in head and "url " not in head)
    )


def clean_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " / ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_readme_catalog(readme: str) -> list[Entry]:
    start = readme.find("## 3️⃣QuantumultX 复写：")
    end = readme.find("## 4️⃣QuantumultX 脚本Task：")
    if start < 0 or end < 0 or end <= start:
        raise SystemExit("Could not locate QuantumultX rewrite table in official README")

    table = readme[start:end]
    current_category = None
    entries: list[Entry] = []

    for row_match in re.finditer(r"<tr>([\s\S]*?)</tr>", table, flags=re.I):
        row = row_match.group(1)
        for category in CATEGORIES:
            if category in clean_html(row):
                current_category = category
                break
        if current_category is None:
            continue

        cells = [m.group(1) for m in re.finditer(r"<td[^>]*>([\s\S]*?)</td>", row, flags=re.I)]
        if not cells:
            continue

        numeric_pos = None
        for pos, cell in enumerate(cells):
            value = clean_html(cell)
            if re.fullmatch(r"\d+", value):
                numeric_pos = pos
                break
        if numeric_pos is None or numeric_pos + 1 >= len(cells):
            continue

        index = int(clean_html(cells[numeric_pos]))
        name_cell = cells[numeric_pos + 1]
        link_cell = cells[numeric_pos + 2] if numeric_pos + 2 < len(cells) else ""

        name = clean_html(name_cell)
        href_match = re.search(r'href="([^"]+)"', link_cell, flags=re.I)
        url = html.unescape(href_match.group(1)) if href_match else None

        em_match = re.search(r"<em>([\s\S]*?)</em>", link_cell, flags=re.I)
        display_file = clean_html(em_match.group(1)) if em_match else None
        if display_file:
            display_file = display_file.replace(" / ", "").strip()

        deprecated = bool(
            re.search(r"<s>|已失效|新版失效|停止维护|未适配新版|官方停止运营", row, flags=re.I)
        )

        entries.append(
            Entry(
                category=current_category,
                index=index,
                name=name,
                url=url,
                display_file=display_file,
                deprecated=deprecated,
            )
        )

    grouped = defaultdict(list)
    for entry in entries:
        grouped[entry.category].append(entry)

    for category, expected in EXPECTED_COUNTS.items():
        items = sorted(grouped.get(category, []), key=lambda item: item.index)
        indexes = [item.index for item in items]
        if len(items) != expected or indexes != list(range(1, expected + 1)):
            raise SystemExit(
                f"Official README catalog changed for {category}: "
                f"expected 1..{expected}, got {indexes}"
            )

    return sorted(entries, key=lambda item: (CATEGORIES.index(item.category), item.index))


def source_basename(entry: Entry) -> str | None:
    if entry.display_file:
        return MIRROR_ALIASES.get(entry.display_file, entry.display_file)
    if entry.url:
        path = urlparse(urldefrag(entry.url)[0]).path.rstrip("/")
        if path:
            name = path.rsplit("/", 1)[-1]
            return MIRROR_ALIASES.get(name, name)
    return None


def get_source(entry: Entry) -> tuple[str, str, str]:
    errors = []

    if entry.url:
        try:
            official = fetch_text(entry.url)
            if looks_like_html(official):
                raise ValueError("official URL returned HTML instead of rules")
            return official, entry.url, "official"
        except Exception as error:
            errors.append(f"official={error}")

        successor_url = SOURCE_OVERRIDES.get(entry.url)
        if successor_url:
            try:
                successor = fetch_text(successor_url)
                if looks_like_html(successor):
                    raise ValueError("successor URL returned HTML instead of rules")
                return successor, successor_url, "successor"
            except Exception as error:
                errors.append(f"successor={error}")

    basename = source_basename(entry)
    if basename:
        mirror_url = MIRROR_BASE + basename
        try:
            mirror = fetch_text(mirror_url)
            if looks_like_html(mirror):
                raise ValueError("mirror returned HTML")
            return mirror, mirror_url, "mirror"
        except Exception as error:
            errors.append(f"mirror={error}")

    raise RuntimeError("; ".join(errors) or "no usable source URL")


def normalize_literal_url_host(pattern: str) -> str:
    separator = r"\/\/"
    start = pattern.find(separator)
    slash_token = r"\/"
    if start >= 0:
        host_start = start + len(separator)
        host_end = pattern.find(slash_token, host_start)
    else:
        separator = "://"
        start = pattern.find(separator)
        slash_token = "/"
        if start < 0:
            return pattern
        host_start = start + len(separator)
        host_end = pattern.find(slash_token, host_start)

    if host_end < 0:
        host_end = len(pattern)

    host_expr = pattern[host_start:host_end]
    literal_host = host_expr.replace(r"\.", ".")
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+(?::[0-9]+)?", literal_host):
        return pattern

    escaped_host = re.sub(r"(?<!\\)\.", r"\\.", host_expr)
    return pattern[:host_start] + escaped_host + pattern[host_end:]


def quote_pattern_if_needed(pattern: str) -> str:
    pattern = pattern.strip()
    quote = None
    inner = pattern
    if len(pattern) >= 2 and pattern[0] == pattern[-1] and pattern[0] in {'"', "'"}:
        quote = pattern[0]
        inner = pattern[1:-1]
    inner = normalize_literal_url_host(inner)
    if "," in inner:
        return '"' + inner.replace('"', r'\"') + '"'
    return quote + inner + quote if quote else inner


def encode_body_token(value: str) -> str:
    return value.replace('"', r"\x22").replace(" ", r"\x20")


def split_script_params(rhs: str):
    parts = []
    start = 0
    quote = None
    escaped = False
    depths = [0, 0, 0]

    for index, char in enumerate(rhs):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "(":
            depths[0] += 1
            continue
        if char == ")":
            depths[0] = max(0, depths[0] - 1)
            continue
        if char == "[":
            depths[1] += 1
            continue
        if char == "]":
            depths[1] = max(0, depths[1] - 1)
            continue
        if char == "{":
            depths[2] += 1
            continue
        if char == "}":
            depths[2] = max(0, depths[2] - 1)
            continue
        if char != "," or any(depths):
            continue

        remainder = rhs[index + 1 :]
        if re.match(r"\s*[A-Za-z][A-Za-z0-9_-]*\s*=", remainder):
            parts.append(rhs[start:index].strip())
            start = index + 1

    parts.append(rhs[start:].strip())
    parsed = []
    for part in parts:
        if "=" not in part:
            raise ValueError(f"invalid Script parameter: {part}")
        key, value = part.split("=", 1)
        parsed.append((key.strip(), value.strip()))
    return parsed


def add_rule(store, section: str, entry: Entry, line: str):
    line = line.strip()
    if not line:
        return
    store[section].append((entry, line))
    entry.counts[section] += 1


def parse_surge_sections(entry: Entry, text: str, store) -> bool:
    section_matches = list(re.finditer(r"^\[([^\]]+)\]\s*$", text, flags=re.M))
    if not section_matches:
        return False

    supported = {"Rule", "URL Rewrite", "Map Local", "Body Rewrite", "Script", "MITM"}
    found_supported = False

    for pos, match in enumerate(section_matches):
        section = match.group(1)
        if section not in supported:
            continue
        found_supported = True
        body_start = match.end()
        body_end = section_matches[pos + 1].start() if pos + 1 < len(section_matches) else len(text)
        body = text[body_start:body_end]

        for raw in body.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";", "//")):
                continue

            if section == "MITM":
                if line.startswith("hostname"):
                    value = line.split("=", 1)[1].strip() if "=" in line else ""
                    if value.startswith("%APPEND%"):
                        value = value[len("%APPEND%") :].strip()
                    for host in value.split(","):
                        host = host.strip()
                        if host:
                            add_rule(store, "MITM", entry, host)
                continue

            if section == "Script":
                try:
                    name, rhs = line.split("=", 1)
                    params = dict(split_script_params(rhs))
                    if "pattern" in params:
                        params["pattern"] = quote_pattern_if_needed(params["pattern"])
                    if params.get("requires-body", "").lower() in {"1", "true"}:
                        params["requires-body"] = "true"
                    elif params.get("requires-body", "").lower() in {"0", "false"}:
                        params.pop("requires-body", None)
                    if params.get("max-size") in {"-1", "0"}:
                        params.pop("max-size", None)
                    if params.get("timeout") in {"5", "60"}:
                        params.pop("timeout", None)
                    rendered = ",".join(f"{key}={value}" for key, value in params.items())
                    add_rule(store, "Script", entry, f"{name.strip()} = {rendered}")
                except Exception:
                    add_rule(store, "Script", entry, line)
                continue

            if section == "URL Rewrite":
                line = re.sub(r"\s+-\s+reject\s*$", " _ reject", line)
            add_rule(store, section, entry, line)

    return found_supported


def parse_qx(entry: Entry, text: str, store):
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "//", "*")):
            continue

        host_match = re.match(r"^hostname\s*=\s*(.+)$", line, flags=re.I)
        if host_match:
            for host in host_match.group(1).split(","):
                host = host.strip()
                if host:
                    add_rule(store, "MITM", entry, host)
            continue

        # QX policy-style rules occasionally live beside rewrites.
        policy_match = re.match(
            r"^(host|host-suffix|host-keyword|ip-cidr|ip6-cidr)\s*,\s*([^,]+)\s*,\s*(direct|reject)\s*$",
            line,
            flags=re.I,
        )
        if policy_match:
            kind, value, policy = policy_match.groups()
            kind_map = {
                "host": "DOMAIN",
                "host-suffix": "DOMAIN-SUFFIX",
                "host-keyword": "DOMAIN-KEYWORD",
                "ip-cidr": "IP-CIDR",
                "ip6-cidr": "IP-CIDR6",
            }
            surge_policy = "DIRECT" if policy.lower() == "direct" else "REJECT"
            add_rule(store, "Rule", entry, f"{kind_map[kind.lower()]},{value.strip()},{surge_policy}")
            continue

        match = re.match(
            r"^(.*?)\s+url\s+(reject(?:-[A-Za-z0-9_-]+)?)\s*$",
            line,
            flags=re.I,
        )
        if match:
            pattern, action = match.groups()
            pattern = normalize_literal_url_host(pattern.strip())
            action = action.lower()
            if action == "reject":
                add_rule(store, "URL Rewrite", entry, f"{pattern} _ reject")
            elif action == "reject-200":
                add_rule(store, "Map Local", entry, f'{pattern} data-type=text data="" status-code=200')
            elif action == "reject-dict":
                add_rule(
                    store,
                    "Map Local",
                    entry,
                    f'{pattern} data-type=text data="{{}}" status-code=200 header="Content-Type:application/json"',
                )
            elif action == "reject-array":
                add_rule(
                    store,
                    "Map Local",
                    entry,
                    f'{pattern} data-type=text data="[]" status-code=200 header="Content-Type:application/json"',
                )
            elif action == "reject-img":
                add_rule(store, "Map Local", entry, f"{pattern} data-type=tiny-gif status-code=200")
            else:
                entry.note += f" unsupported:{action}"
            continue

        match = re.match(
            r"^(.*?)\s+url\s+"
            r"(script-response-body|script-request-body|script-echo-response|"
            r"script-request-header|script-response-header|script-analyze-echo-response)"
            r"\s+(\S+)\s*$",
            line,
            flags=re.I,
        )
        if match:
            pattern, mode, script_path = match.groups()
            mode = mode.lower()
            script_type = "http-response" if mode in {
                "script-response-body",
                "script-echo-response",
                "script-response-header",
                "script-analyze-echo-response",
            } else "http-request"
            requires_body = mode in {
                "script-response-body",
                "script-request-body",
                "script-echo-response",
                "script-analyze-echo-response",
            }
            base = urlparse(script_path).path.rsplit("/", 1)[-1] or "script"
            base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_") or "script"
            params = [
                f"type={script_type}",
                f"pattern={quote_pattern_if_needed(pattern)}",
                f"script-path={script_path}",
            ]
            if requires_body:
                params.append("requires-body=true")
            add_rule(store, "Script", entry, f"{base} = " + ",".join(params))
            continue

        match = re.match(
            r"^(.*?)\s+url\s+(jsonjq-response-body|jsonjq-request-body)\s+(.+)$",
            line,
            flags=re.I,
        )
        if match:
            pattern, mode, expression = match.groups()
            direction = "http-response-jq" if "response" in mode.lower() else "http-request-jq"
            add_rule(
                store,
                "Body Rewrite",
                entry,
                f"{direction} {normalize_literal_url_host(pattern.strip())} {expression.strip()}",
            )
            continue

        match = re.match(r"^(.*?)\s+url\s+(302|307)\s+(\S+)\s*$", line, flags=re.I)
        if match:
            pattern, code, replacement = match.groups()
            add_rule(
                store,
                "URL Rewrite",
                entry,
                f"{normalize_literal_url_host(pattern.strip())} {replacement} {code}",
            )
            continue

        match = re.match(
            r"^(.*?)\s+url\s+response-body\s+(\S+)\s+response-body\s+(\S+)\s*$",
            line,
            flags=re.I,
        )
        if match:
            pattern, search, replacement = match.groups()
            add_rule(
                store,
                "Body Rewrite",
                entry,
                " ".join(
                    [
                        "http-response",
                        normalize_literal_url_host(pattern.strip()),
                        encode_body_token(search),
                        encode_body_token(replacement),
                    ]
                ),
            )
            continue

        match = re.match(
            r"^(.*?)\s+url\s+echo-response\s+(\S+)\s+echo-response\s+(.+)$",
            line,
            flags=re.I,
        )
        if match:
            pattern, mime, data = match.groups()
            escaped = data.replace('"', r'\"')
            add_rule(
                store,
                "Map Local",
                entry,
                f'{normalize_literal_url_host(pattern.strip())} data-type=text data="{escaped}" '
                f'status-code=200 header="Content-Type:{mime}"',
            )
            continue

        # Native Surge URL rewrite lines sometimes appear without a section in mirrored files.
        if re.search(r"\s+_\s+reject\s*$", line):
            add_rule(store, "URL Rewrite", entry, line)
            continue

        if " url " in line:
            entry.note += " unsupported-qx-line"


def dedupe_store(store):
    result = {}
    for section, rows in store.items():
        seen = set()
        kept = []
        for entry, line in rows:
            key = line
            if section == "MITM":
                key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            kept.append((entry, line))
        result[section] = kept
    return result


def unique_script_names(rows):
    parsed = []
    counts = Counter()
    for entry, line in rows:
        if "=" not in line:
            parsed.append((entry, "script", line))
            counts["script"] += 1
            continue
        name, rhs = line.split("=", 1)
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_") or "script"
        parsed.append((entry, safe, rhs.strip()))
        counts[safe] += 1

    used = defaultdict(int)
    result = []
    for entry, base, rhs in parsed:
        used[base] += 1
        name = base if counts[base] == 1 else f"{base}_{used[base]}"
        result.append((entry, f"{name} = {rhs}"))
    return result


def render_section(section: str, rows):
    out = [f"[{section}]"]
    current = None
    for entry, line in rows:
        marker = (entry.category, entry.index, entry.name)
        if marker != current:
            if len(out) > 1:
                out.append("")
            out.append(f"# ===== {entry.category} {entry.index}. {entry.name} =====")
            out.append(f"# source: {entry.source_used or entry.url or 'N/A'}")
            current = marker
        out.append(line)
    out.append("")
    return out


def build_module(entries: list[Entry]) -> str:
    store = defaultdict(list)

    fetchable = []
    for entry in entries:
        if entry.name in DIRECTORY_ONLY_NAMES:
            entry.status = "CATALOG_ONLY"
            entry.note = "README directory/homepage entry; no single executable rule source"
        elif entry.deprecated:
            entry.status = "DEPRECATED"
            entry.note = "Official README marks this entry deprecated/unavailable"
        elif not entry.url:
            entry.status = "NO_URL"
            entry.note = "No source URL in official README"
        else:
            fetchable.append(entry)

    fetched = {}
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(fetchable)))) as executor:
        futures = {executor.submit(get_source, entry): entry for entry in fetchable}
        for future in as_completed(futures):
            entry = futures[future]
            key = (entry.category, entry.index)
            try:
                fetched[key] = future.result()
            except Exception as error:
                fetched[key] = error

    for entry in fetchable:
        value = fetched[(entry.category, entry.index)]
        if isinstance(value, Exception):
            entry.status = "UNAVAILABLE"
            entry.note = str(value)
            continue

        source, source_used, source_mode = value
        entry.source_used = source_used
        entry.source_mode = source_mode
        before = sum(entry.counts.values())
        parsed_native = parse_surge_sections(entry, source, store)
        if not parsed_native or sum(entry.counts.values()) == before:
            parse_qx(entry, source, store)

        produced = sum(entry.counts.values())
        if produced > 0:
            entry.status = "ACTIVE"
        else:
            entry.status = "NO_RULES"
            entry.note = (entry.note + " source fetched but no executable trigger rules parsed").strip()

    store = dedupe_store(store)
    store["Script"] = unique_script_names(store.get("Script", []))

    # Normalize MITM into one %APPEND% line while preserving provenance in the catalog.
    hosts = sorted({line for _, line in store.get("MITM", [])}, key=str.lower)
    mitm_rows = []
    if hosts:
        synthetic_entry = next((e for e in entries if e.status == "ACTIVE"), entries[0])
        mitm_rows = [(synthetic_entry, "hostname = %APPEND% " + ",".join(hosts))]

    header = [
        "#!name=墨鱼全功能合集（Surge兼容版）",
        "#!desc=按 ddgksf2013 官方 README 四大类自动同步：会员解锁 / 广告屏蔽 / 应用增强 / 网页优化",
        "#!author=ddgksf2013 + upstream contributors",
        "#!homepage=https://github.com/ddgksf2013/ddgksf2013",
        f"#!raw-url={PUBLIC_RAW_URL}",
        "#!category=墨鱼合集",
        "#!requirement=CORE_VERSION>=20",
        "#!remark=由 mrtanshiyue/Amazon 自动维护；官方 README 为目录源，官方条目源优先，无法机器读取时回退到 ifflagged/Romeo 的 ddgksf2013 Surge 镜像；已失效条目仅保留目录记录。",
        "",
        "# ==================== README CATALOG COVERAGE ====================",
    ]

    for category in CATEGORIES:
        category_entries = [e for e in entries if e.category == category]
        active = sum(e.status == "ACTIVE" for e in category_entries)
        header.append(
            f"# {category}: {len(category_entries)}/{EXPECTED_COUNTS[category]} cataloged, "
            f"{active} active"
        )
        for entry in category_entries:
            mode = f" via {entry.source_mode}" if entry.source_mode else ""
            detail = f" | {entry.note}" if entry.note else ""
            header.append(
                f"#   {entry.index:02d}. [{entry.status}]{mode} {entry.name}"
                f" | {entry.source_used or entry.url or 'N/A'}{detail}"
            )
    header.extend(["# ================================================================", ""])

    output = list(header)
    for section in ["Rule", "URL Rewrite", "Map Local", "Body Rewrite", "Script"]:
        output.extend(render_section(section, store.get(section, [])))
    output.extend(render_section("MITM", mitm_rows))

    result = "\n".join(output).rstrip() + "\n"
    validate_result(result, entries, store, hosts)
    return result


def validate_result(result: str, entries: list[Entry], store, hosts):
    for category, expected in EXPECTED_COUNTS.items():
        count = sum(entry.category == category for entry in entries)
        if count != expected:
            raise SystemExit(f"Catalog count mismatch for {category}: {count} != {expected}")

    if "#!name=墨鱼全功能合集（Surge兼容版）" not in result:
        raise SystemExit("Generated module header missing")
    if f"#!raw-url={PUBLIC_RAW_URL}" not in result:
        raise SystemExit("Generated raw-url missing")

    for section in ["Rule", "URL Rewrite", "Map Local", "Body Rewrite", "Script", "MITM"]:
        if result.count(f"[{section}]") != 1:
            raise SystemExit(f"Missing/duplicate [{section}] section")

    active_entries = [entry for entry in entries if entry.status == "ACTIVE"]
    non_deprecated_concrete = [
        entry for entry in entries
        if not entry.deprecated and entry.name not in DIRECTORY_ONLY_NAMES and entry.url
    ]

    # Fail loudly if the source ecosystem changes enough to silently drop a large chunk.
    if len(active_entries) < max(45, int(len(non_deprecated_concrete) * 0.70)):
        inactive = ", ".join(
            f"{e.category}{e.index}:{e.name}={e.status}"
            for e in non_deprecated_concrete if e.status != "ACTIVE"
        )
        raise SystemExit(
            f"Too few README entries converted ({len(active_entries)}/"
            f"{len(non_deprecated_concrete)}). Inactive: {inactive}"
        )

    if len(store.get("Script", [])) < 20:
        raise SystemExit("Generated Script section unexpectedly small")
    if len(store.get("URL Rewrite", [])) + len(store.get("Map Local", [])) < 100:
        raise SystemExit("Generated rewrite coverage unexpectedly small")
    if len(hosts) < 100:
        raise SystemExit("Generated MITM hostname coverage unexpectedly small")

    if re.search(r"\s+-\s+reject(?:-[A-Za-z0-9_-]+)?\s*$", result, flags=re.M):
        raise SystemExit("Legacy QX '- reject' syntax survived conversion")
    if "max-size=-1" in result:
        raise SystemExit("Unlimited HTTP Script body setting survived conversion")
    if "timeout=60" in result:
        raise SystemExit("Converter-default timeout=60 survived conversion")


def main():
    readme = fetch_text(README_URL)
    entries = parse_readme_catalog(readme)

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    existing = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    result = build_module(entries)

    if existing == result:
        print("No catalog/source changes.")
        return

    TARGET.write_text(result, encoding="utf-8")

    statuses = Counter(entry.status for entry in entries)
    print(f"Updated {TARGET}")
    print("Catalog:", ", ".join(f"{k}={v}" for k, v in EXPECTED_COUNTS.items()))
    print("Statuses:", ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())))
    print(f"Module size: {len(result)} chars")


if __name__ == "__main__":
    main()
