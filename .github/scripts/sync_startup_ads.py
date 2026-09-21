#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

TARGET = Path("modules/StartUpAds-Surge.sgmodule")
PUBLIC_RAW_URL = "https://raw.githubusercontent.com/mrtanshiyue/Amazon/main/modules/StartUpAds-Surge.sgmodule"
USER_AGENT = "mrtanshiyue-amazon-moyu-adblock-sync/3.0"

# This is the original source list used by the MoYu/墨鱼 Adblock.sgmodule generator.
# The old ddgksf2013/Modules endpoint is no longer directly readable, so sync the
# maintained source files themselves instead of depending on a stale mirror.
SOURCE_URLS = [
    "https://ddgksf2013.top/rewrite/StartUpAds.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/Ximalaya.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/BilibiliAds.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/Weibo.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/KeepAds.conf",
    "https://gist.githubusercontent.com/ddgksf2013/d43179d848586d561dbb968dee93bae8/raw/Zhihu.Adblock.js",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/Amap.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/XiaoHongShuAds.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/NeteaseAds.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/SmzdmAds.conf",
    "https://gist.githubusercontent.com/ddgksf2013/bb1dadbd32f67c68772caebcc70b0a33/raw/pipixia.adblock.js",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/CaiYunAds.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/CainiaoAds.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/Html/Douban.conf",
    "https://raw.githubusercontent.com/app2smile/rules/master/module/qidian.conf",
    "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/SuiShouJi.conf",
    "https://raw.githubusercontent.com/app2smile/rules/master/module/qqnews.conf",
    "https://gist.githubusercontent.com/ddgksf2013/f43026707830c7818ee3ba624e383c8d/raw/baiduCloud.adblock.js",
]

EXCLUDED_MITM_HOSTS = {"api-sams.walmartmobile.cn"}
MIN_URL_REWRITES = 30
MIN_SCRIPTS = 40
MIN_MITM_HOSTS = 20
MAX_DROP_RATIO = 0.35

REJECT_RE = re.compile(
    r"^(.*?)\s+url\s+(reject(?:-[A-Za-z0-9_-]+)?)\s*$",
    re.IGNORECASE,
)
SCRIPT_RE = re.compile(
    r"^(.*?)\s+url\s+"
    r"(script-response-body|script-request-body|script-echo-response|"
    r"script-request-header|script-response-header|script-analyze-echo-response)"
    r"\s+(\S+)\s*$",
    re.IGNORECASE,
)
BODY_RE = re.compile(
    r"^(.*?)\s+url\s+response-body\s+(\S+)\s+response-body\s+(\S+)\s*$",
    re.IGNORECASE,
)
ECHO_RE = re.compile(
    r"^(.*?)\s+url\s+echo-response\s+(\S+)\s+echo-response\s+(\S+)\s*$",
    re.IGNORECASE,
)
HOST_RE = re.compile(r"^\s*hostname\s*=\s*([^#\n]+)", re.IGNORECASE)
QX_REDIRECT_RE = re.compile(r"^.*?\s+url\s+(?:302|307)\s+\S+\s*$", re.IGNORECASE)


def fetch_text(url: str, attempts: int = 3, timeout: int = 30) -> str:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8-sig").replace("\r\n", "\n")
                if not text.strip():
                    raise ValueError("empty response")
                return text
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise SystemExit(f"Failed to fetch {url} after {attempts} attempts: {last_error}")


def source_label(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else urlparse(url).netloc
    return name or urlparse(url).netloc


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
    quote = None
    inner = pattern
    if len(pattern) >= 2 and pattern[0] == pattern[-1] and pattern[0] in {'"', "'"}:
        quote = pattern[0]
        inner = pattern[1:-1]

    inner = normalize_literal_url_host(inner)
    if "," in inner:
        return '"' + inner.replace('"', r'\"') + '"'
    if quote:
        return quote + inner + quote
    return inner


def encode_body_token(value: str) -> str:
    return value.replace('"', r"\x22").replace(" ", r"\x20")


def map_local_for_reject(pattern: str, action: str) -> str:
    pattern = normalize_literal_url_host(pattern)
    action = action.lower()
    if action == "reject-200":
        return f'{pattern} data-type=text data="" status-code=200'
    if action == "reject-dict":
        return (
            f'{pattern} data-type=text data="{{}}" status-code=200 '
            'header="Content-Type:application/json"'
        )
    if action == "reject-array":
        return (
            f'{pattern} data-type=text data="[]" status-code=200 '
            'header="Content-Type:application/json"'
        )
    if action == "reject-img":
        return f"{pattern} data-type=tiny-gif status-code=200"
    raise SystemExit(f"Unsupported reject action: {action}")


def escape_map_data(value: str) -> str:
    return value.replace('"', r'\"')


def parse_source(url: str, text: str):
    label = source_label(url)
    url_rewrites = []
    map_local = []
    body_rewrites = []
    scripts = []
    hosts = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        host_match = HOST_RE.match(line)
        if host_match:
            hosts.extend(host.strip() for host in host_match.group(1).split(",") if host.strip())
            continue

        if line.startswith(("#", ";", "//")):
            continue

        match = BODY_RE.match(line)
        if match:
            pattern, search, replacement = match.groups()
            body_rewrites.append(
                (
                    label,
                    " ".join(
                        [
                            "http-response",
                            normalize_literal_url_host(pattern.strip()),
                            encode_body_token(search),
                            encode_body_token(replacement),
                        ]
                    ),
                )
            )
            continue

        match = ECHO_RE.match(line)
        if match:
            pattern, mime_type, data = match.groups()
            map_local.append(
                (
                    label,
                    f'{normalize_literal_url_host(pattern.strip())} '
                    f'data-type=text data="{escape_map_data(data)}" status-code=200 '
                    f'header="Content-Type:{mime_type}"',
                )
            )
            continue

        match = SCRIPT_RE.match(line)
        if match:
            pattern, script_type_raw, script_path = match.groups()
            raw_type = script_type_raw.lower()
            script_type = "http-response" if raw_type in {
                "script-response-body",
                "script-echo-response",
                "script-response-header",
                "script-analyze-echo-response",
            } else "http-request"
            requires_body = raw_type in {
                "script-response-body",
                "script-request-body",
                "script-echo-response",
                "script-analyze-echo-response",
            }
            scripts.append(
                (
                    label,
                    script_type,
                    quote_pattern_if_needed(pattern.strip()),
                    script_path.strip(),
                    requires_body,
                )
            )
            continue

        match = REJECT_RE.match(line)
        if match:
            pattern, action = match.groups()
            pattern = pattern.strip()
            action = action.lower()
            if action == "reject":
                url_rewrites.append(
                    (label, f"{normalize_literal_url_host(pattern)} _ reject")
                )
            else:
                map_local.append((label, map_local_for_reject(pattern, action)))
            continue

        # The original MoYu fusion generator intentionally ignored QX redirect
        # rules; preserve that behavior instead of inventing a Surge equivalent.
        if QX_REDIRECT_RE.match(line):
            print(f"Ignored QX redirect from {label}: {line}")
            continue

        if " url " in line:
            raise SystemExit(f"Unsupported QX rewrite syntax in {label}: {line}")

    return url_rewrites, map_local, body_rewrites, scripts, hosts


def dedupe_pairs(items):
    seen = set()
    result = []
    for label, line in items:
        if line in seen:
            continue
        seen.add(line)
        result.append((label, line))
    return result


def dedupe_scripts(items):
    seen = set()
    result = []
    for item in items:
        label, script_type, pattern, script_path, requires_body = item
        key = (script_type, pattern, script_path, requires_body)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def render_grouped(items):
    output = []
    current_label = None
    for label, line in items:
        if label != current_label:
            if output:
                output.append("")
            output.append(f"# ===== {label} =====")
            current_label = label
        output.append(line)
    return output


def script_base_name(script_path: str) -> str:
    name = urlparse(script_path).path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_")
    return stem or "script"


def render_scripts(items):
    bases = [script_base_name(item[3]) for item in items]
    totals = Counter(bases)
    used = defaultdict(int)
    output = []
    current_label = None

    for label, script_type, pattern, script_path, requires_body in items:
        base = script_base_name(script_path)
        used[base] += 1
        name = base if totals[base] == 1 else f"{base}_{used[base]}"

        params = [
            f"type={script_type}",
            f"pattern={pattern}",
            f"script-path={script_path}",
        ]
        if requires_body:
            params.append("requires-body=true")

        if label != current_label:
            if output:
                output.append("")
            output.append(f"# ===== {label} =====")
            current_label = label

        output.append(f"{name} = " + ",".join(params))

    return output


def active_section_lines(text: str, section: str):
    marker = f"[{section}]"
    if marker not in text:
        return []
    tail = text.split(marker, 1)[1]
    next_section = re.search(r"^\[[^\]]+\]\s*$", tail, re.MULTILINE)
    body = tail[: next_section.start()] if next_section else tail
    return [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def mitm_hosts(text: str):
    match = re.search(r"^hostname\s*=\s*%APPEND%\s+(.+)$", text, re.MULTILINE)
    if not match:
        return []
    return [host.strip() for host in match.group(1).split(",") if host.strip()]


def validate_result(result: str, existing: str):
    required_sections = [
        "General",
        "Rule",
        "URL Rewrite",
        "Map Local",
        "Body Rewrite",
        "Script",
        "MITM",
    ]
    for section in required_sections:
        if result.count(f"[{section}]") != 1:
            raise SystemExit(f"Unexpected duplicate/missing [{section}] section")

    required_headers = [
        "#!name=墨鱼去广告模块（Surge兼容版）",
        f"#!raw-url={PUBLIC_RAW_URL}",
        "#!requirement=CORE_VERSION>=20",
    ]
    for item in required_headers:
        if item not in result:
            raise SystemExit(f"Missing required header: {item}")

    url_count = len(active_section_lines(result, "URL Rewrite"))
    script_count = len(active_section_lines(result, "Script"))
    host_count = len(mitm_hosts(result))

    if url_count < MIN_URL_REWRITES:
        raise SystemExit(f"Too few URL Rewrite rules: {url_count} < {MIN_URL_REWRITES}")
    if script_count < MIN_SCRIPTS:
        raise SystemExit(f"Too few Script rules: {script_count} < {MIN_SCRIPTS}")
    if host_count < MIN_MITM_HOSTS:
        raise SystemExit(f"Too few MITM hosts: {host_count} < {MIN_MITM_HOSTS}")

    if re.search(r"\s-\sreject(?:-[A-Za-z0-9_-]+)?\s*$", result, re.MULTILINE):
        raise SystemExit("Legacy '- reject' syntax survived conversion")
    if re.search(r"\surl\s+(?:reject|script-|response-body|echo-response)", result, re.IGNORECASE):
        raise SystemExit("QX rewrite syntax survived conversion")
    if "max-size=-1" in result:
        raise SystemExit("Unlimited HTTP Script body size survived conversion")
    if "timeout=60" in result:
        raise SystemExit("Converter-default Script timeout survived conversion")

    script_names = []
    for line in active_section_lines(result, "Script"):
        if "=" not in line:
            raise SystemExit(f"Invalid Script entry: {line}")
        name, rhs = line.split("=", 1)
        name = name.strip()
        script_names.append(name)
        if "type=http-" not in rhs or "pattern=" not in rhs or "script-path=" not in rhs:
            raise SystemExit(f"Invalid Script parameters: {line}")

    duplicates = [name for name, count in Counter(script_names).items() if count > 1]
    if duplicates:
        raise SystemExit(f"Duplicate Script names: {', '.join(sorted(duplicates))}")

    if existing and "#!name=墨鱼去广告模块（Surge兼容版）" in existing:
        old_url_count = len(active_section_lines(existing, "URL Rewrite"))
        old_script_count = len(active_section_lines(existing, "Script"))
        old_host_count = len(mitm_hosts(existing))

        for name, old, new in [
            ("URL Rewrite", old_url_count, url_count),
            ("Script", old_script_count, script_count),
            ("MITM hosts", old_host_count, host_count),
        ]:
            if old >= 10 and new < old * (1 - MAX_DROP_RATIO):
                raise SystemExit(f"{name} shrank too much ({old} -> {new}); keeping previous module")


def script_urls(result: str):
    urls = []
    for line in active_section_lines(result, "Script"):
        match = re.search(r"(?:^|,)script-path=([^,]+)", line.split("=", 1)[1])
        if match:
            value = match.group(1).strip()
            if value.startswith(("https://", "http://")):
                urls.append(value)
    return sorted(set(urls))


def check_remote_script(url: str, attempts: int = 3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Range": "bytes=0-2047",
                    "Accept": "text/plain,*/*;q=0.8",
                },
            )
            with urlopen(request, timeout=20) as response:
                sample = response.read(2048)
                if not sample:
                    raise ValueError("empty response")
                return
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"{url}: {last_error}")


def validate_remote_scripts(result: str):
    urls = script_urls(result)
    if len(urls) < 10:
        raise SystemExit(f"Unexpectedly few unique remote scripts: {len(urls)}")

    failures = []
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as executor:
        futures = {executor.submit(check_remote_script, url): url for url in urls}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:
                failures.append(str(error))

    if failures:
        details = "\n".join(f"  - {failure}" for failure in sorted(failures))
        raise SystemExit(
            "Remote Script health check failed after retries; keeping previous module:\n" + details
        )

    print(f"Remote Script health check passed for {len(urls)} unique URLs.")


def build_module(source_texts):
    all_url_rewrites = []
    all_map_local = []
    all_body_rewrites = []
    all_scripts = []
    all_hosts = []

    for url, text in source_texts:
        url_rewrites, map_local, body_rewrites, scripts, hosts = parse_source(url, text)
        all_url_rewrites.extend(url_rewrites)
        all_map_local.extend(map_local)
        all_body_rewrites.extend(body_rewrites)
        all_scripts.extend(scripts)
        all_hosts.extend(hosts)

    all_url_rewrites = dedupe_pairs(all_url_rewrites)
    all_map_local = dedupe_pairs(all_map_local)
    all_body_rewrites = dedupe_pairs(all_body_rewrites)
    all_scripts = dedupe_scripts(all_scripts)

    hosts = sorted(
        {
            host.strip()
            for host in all_hosts
            if host.strip() and host.strip().lower() not in EXCLUDED_MITM_HOSTS
        },
        key=str.lower,
    )

    header = [
        "#!name=墨鱼去广告模块（Surge兼容版）",
        "#!desc=同步墨鱼融合版原始源，并按本仓库规则转换为 Surge 当前兼容语法",
        "#!author=ddgksf2013",
        "#!contributor=@blackmatrix7, @app2smile",
        "#!homepage=https://github.com/ddgksf2013",
        "#!tgchannel=https://t.me/ddgksf2021",
        f"#!raw-url={PUBLIC_RAW_URL}",
        "#!category=去广告",
        "#!requirement=CORE_VERSION>=20",
        "#!remark=由 mrtanshiyue/Amazon 自动维护；同步墨鱼融合版原始源，转换 QX Rewrite/Map Local/Body Rewrite/HTTP Script 为 Surge 语法，并移除无限 body 缓冲与转换器默认长超时。",
        "",
        "# Original MoYu fusion sources:",
        *[f"# - {url}" for url in SOURCE_URLS],
        "",
    ]

    output = list(header)
    output.extend(["[General]", "", "[Rule]", ""])

    output.append("[URL Rewrite]")
    output.extend(render_grouped(all_url_rewrites))
    output.append("")

    output.append("[Map Local]")
    output.extend(render_grouped(all_map_local))
    output.append("")

    output.append("[Body Rewrite]")
    output.extend(render_grouped(all_body_rewrites))
    output.append("")

    output.append("[Script]")
    output.extend(render_scripts(all_scripts))
    output.append("")

    output.extend(["[MITM]", "", "hostname = %APPEND% " + ",".join(hosts), ""])

    return "\n".join(output).rstrip() + "\n"


def main():
    source_texts = []
    total_chars = 0

    for url in SOURCE_URLS:
        text = fetch_text(url)
        total_chars += len(text)
        source_texts.append((url, text))
        print(f"Fetched {source_label(url)} ({len(text)} chars)")

    if len(source_texts) != len(SOURCE_URLS):
        raise SystemExit("Not all MoYu source files were fetched")
    if total_chars < 20_000:
        raise SystemExit(f"Combined MoYu sources unexpectedly small: {total_chars} chars")

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    existing = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""

    result = build_module(source_texts)
    validate_result(result, existing)

    if os.environ.get("SKIP_REMOTE_SCRIPT_CHECK") != "1":
        validate_remote_scripts(result)

    if existing == result:
        print("No upstream or conversion changes.")
        return

    TARGET.write_text(result, encoding="utf-8")
    print(
        f"Updated {TARGET}: "
        f"{len(active_section_lines(result, 'URL Rewrite'))} rewrites, "
        f"{len(active_section_lines(result, 'Map Local'))} map-local rules, "
        f"{len(active_section_lines(result, 'Body Rewrite'))} body rewrites, "
        f"{len(active_section_lines(result, 'Script'))} scripts, "
        f"{len(mitm_hosts(result))} MITM hosts"
    )


if __name__ == "__main__":
    main()
