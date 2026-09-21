#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE = "https://raw.githubusercontent.com/MeChenCC/Modules/main/Adblock.sgmodule"
TARGET = Path("modules/StartUpAds-Surge.sgmodule")
PUBLIC_RAW_URL = "https://raw.githubusercontent.com/mrtanshiyue/Amazon/main/modules/StartUpAds-Surge.sgmodule"
USER_AGENT = "mrtanshiyue-amazon-moyu-adblock-sync/4.0"

MIN_SOURCE_BYTES = 15_000
MIN_URL_REWRITES = 40
MIN_SCRIPTS = 50
MIN_MITM_HOSTS = 20
MAX_DROP_RATIO = 0.35
EXCLUDED_MITM_HOSTS = {"api-sams.walmartmobile.cn"}


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


def split_sections(text: str):
    order = []
    sections = {}
    current = None
    for line in text.splitlines():
        match = re.match(r"^\[([^\]]+)\]\s*$", line.strip())
        if match:
            current = match.group(1)
            if current not in sections:
                sections[current] = []
                order.append(current)
            continue
        if current is not None:
            sections[current].append(line)
    return order, sections


def active_lines(lines):
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


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


def split_script_params(rhs: str):
    parts = []
    start = 0
    quote = None
    escaped = False
    depth_round = depth_square = depth_curly = 0

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
            depth_round += 1
            continue
        if char == ")":
            depth_round = max(0, depth_round - 1)
            continue
        if char == "[":
            depth_square += 1
            continue
        if char == "]":
            depth_square = max(0, depth_square - 1)
            continue
        if char == "{":
            depth_curly += 1
            continue
        if char == "}":
            depth_curly = max(0, depth_curly - 1)
            continue
        if char != "," or any((depth_round, depth_square, depth_curly)):
            continue

        remainder = rhs[index + 1 :]
        if re.match(r"\s*[A-Za-z][A-Za-z0-9_-]*\s*=", remainder):
            parts.append(rhs[start:index].strip())
            start = index + 1

    parts.append(rhs[start:].strip())

    parsed = []
    seen = set()
    for part in parts:
        if "=" not in part:
            raise SystemExit(f"Invalid Script parameter: {part}")
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key):
            raise SystemExit(f"Invalid Script parameter name: {key}")
        if key in seen:
            raise SystemExit(f"Duplicate Script parameter: {key}")
        seen.add(key)
        parsed.append((key, value))
    return parsed


def parse_script_line(line: str):
    match = re.match(r"^(.*?)\s*=\s*(.+)$", line.strip())
    if not match:
        raise SystemExit(f"Unsupported Script syntax: {line}")
    name, rhs = match.groups()
    return name.strip(), split_script_params(rhs)


def normalize_script_spec(line: str):
    original_name, items = parse_script_line(line)
    values = dict(items)

    script_type = values.get("type")
    if script_type not in {"http-request", "http-response"}:
        raise SystemExit(f"Unsupported HTTP Script type in {original_name}: {script_type}")
    if "pattern" not in values or "script-path" not in values:
        raise SystemExit(f"Script missing pattern/script-path: {line}")

    values["pattern"] = quote_pattern_if_needed(values["pattern"])

    if values.get("requires-body", "").lower() in {"false", "0"}:
        values.pop("requires-body", None)
    elif values.get("requires-body", "").lower() in {"true", "1"}:
        values["requires-body"] = "true"

    if values.get("max-size") in {"-1", "0"}:
        values.pop("max-size", None)
    if values.get("timeout") in {"5", "60"}:
        values.pop("timeout", None)

    preferred = [
        "type",
        "pattern",
        "script-path",
        "requires-body",
        "max-size",
        "timeout",
        "script-update-interval",
        "argument",
        "engine",
        "debug",
        "binary-body-mode",
        "full-header-mode",
    ]
    original_order = [key for key, _ in items]
    ordered_keys = [key for key in preferred if key in values]
    ordered_keys.extend(key for key in original_order if key in values and key not in ordered_keys)

    semantic_key = tuple((key, values[key]) for key in ordered_keys)
    return original_name, semantic_key, values, ordered_keys


def safe_script_name(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[^A-Za-z0-9_\-]+", "_", value).strip("_")
    return value or "script"


def normalize_scripts(lines):
    specs = []
    seen_semantics = set()
    for line in active_lines(lines):
        original_name, semantic_key, values, ordered_keys = normalize_script_spec(line)
        if semantic_key in seen_semantics:
            continue
        seen_semantics.add(semantic_key)
        specs.append((safe_script_name(original_name), values, ordered_keys))

    totals = Counter(name for name, _, _ in specs)
    used = defaultdict(int)
    output = []
    for base, values, ordered_keys in specs:
        used[base] += 1
        name = base if totals[base] == 1 else f"{base}_{used[base]}"
        rendered = ",".join(f"{key}={values[key]}" for key in ordered_keys)
        output.append(f"{name} = {rendered}")
    return output


def normalize_url_rewrites(lines):
    output = []
    seen = set()
    for raw in active_lines(lines):
        match = re.match(
            r"^(.*?)\s+(?:-|_)\s+(reject(?:-[A-Za-z0-9_-]+)?)\s*$",
            raw,
            re.IGNORECASE,
        )
        if not match:
            raise SystemExit(f"Unsupported URL Rewrite syntax: {raw}")
        pattern, action = match.groups()
        action = action.lower()
        if action != "reject":
            raise SystemExit(f"Unexpected non-reject action in mirror URL Rewrite: {raw}")
        line = f"{normalize_literal_url_host(pattern.strip())} _ reject"
        if line not in seen:
            seen.add(line)
            output.append(line)
    return output


def normalize_map_local(lines):
    output = []
    seen = set()
    for raw in active_lines(lines):
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            raise SystemExit(f"Invalid Map Local line: {raw}")
        line = f"{normalize_literal_url_host(parts[0])} {parts[1]}"
        if line not in seen:
            seen.add(line)
            output.append(line)
    return output


def normalize_mitm(lines):
    hosts = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped.startswith("hostname ="):
            continue
        value = stripped.split("=", 1)[1].strip()
        if value.startswith("%APPEND%"):
            value = value[len("%APPEND%") :].strip()
        hosts.extend(host.strip() for host in value.split(",") if host.strip())

    return sorted(
        {
            host
            for host in hosts
            if host.lower() not in EXCLUDED_MITM_HOSTS
        },
        key=str.lower,
    )


def section_active_count(text: str, section: str) -> int:
    _, sections = split_sections(text)
    return len(active_lines(sections.get(section, [])))


def mitm_host_count(text: str) -> int:
    _, sections = split_sections(text)
    return len(normalize_mitm(sections.get("MITM", [])))


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

    for required in [
        "#!name=墨鱼去广告模块（Surge兼容版）",
        f"#!raw-url={PUBLIC_RAW_URL}",
        "#!requirement=CORE_VERSION>=20",
    ]:
        if required not in result:
            raise SystemExit(f"Missing required content: {required}")

    url_count = section_active_count(result, "URL Rewrite")
    script_count = section_active_count(result, "Script")
    host_count = mitm_host_count(result)

    if url_count < MIN_URL_REWRITES:
        raise SystemExit(f"URL Rewrite unexpectedly small: {url_count} < {MIN_URL_REWRITES}")
    if script_count < MIN_SCRIPTS:
        raise SystemExit(f"Script section unexpectedly small: {script_count} < {MIN_SCRIPTS}")
    if host_count < MIN_MITM_HOSTS:
        raise SystemExit(f"MITM hostname list unexpectedly small: {host_count} < {MIN_MITM_HOSTS}")

    if re.search(r"\s-\sreject(?:-[A-Za-z0-9_-]+)?\s*$", result, re.MULTILINE):
        raise SystemExit("Legacy '- reject' syntax survived conversion")
    if "max-size=-1" in result:
        raise SystemExit("Unlimited HTTP Script body survived conversion")
    if "timeout=60" in result:
        raise SystemExit("Converter-default Script timeout survived conversion")

    script_names = []
    _, sections = split_sections(result)
    for line in active_lines(sections.get("Script", [])):
        name, _ = parse_script_line(line)
        script_names.append(name)
    duplicates = [name for name, count in Counter(script_names).items() if count > 1]
    if duplicates:
        raise SystemExit(f"Duplicate generated Script names: {', '.join(sorted(duplicates))}")

    if existing and "#!name=墨鱼去广告模块（Surge兼容版）" in existing:
        for section in ["URL Rewrite", "Script"]:
            old = section_active_count(existing, section)
            new = section_active_count(result, section)
            if old >= 10 and new < old * (1 - MAX_DROP_RATIO):
                raise SystemExit(f"[{section}] shrank too much ({old} -> {new}); keeping previous module")
        old_hosts = mitm_host_count(existing)
        if old_hosts >= 10 and host_count < old_hosts * (1 - MAX_DROP_RATIO):
            raise SystemExit(
                f"MITM hostname list shrank too much ({old_hosts} -> {host_count}); keeping previous module"
            )


def script_urls(result: str):
    _, sections = split_sections(result)
    urls = []
    for line in active_lines(sections.get("Script", [])):
        _, params = parse_script_line(line)
        value = dict(params).get("script-path", "")
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
                if not response.read(2048):
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


def convert(source: str) -> str:
    if len(source) < MIN_SOURCE_BYTES:
        raise SystemExit(f"Unexpectedly short MoYu mirror module: {len(source)} chars")
    if "#!name=墨鱼去广告模块" not in source:
        raise SystemExit("Unexpected MoYu mirror module header")

    _, sections = split_sections(source)
    for section in ["URL Rewrite", "Script", "MITM"]:
        if section not in sections:
            raise SystemExit(f"Required upstream section missing: [{section}]")

    url_rewrites = normalize_url_rewrites(sections.get("URL Rewrite", []))
    map_local = normalize_map_local(sections.get("Map Local", []))
    scripts = normalize_scripts(sections.get("Script", []))
    hosts = normalize_mitm(sections.get("MITM", []))

    general = active_lines(sections.get("General", []))
    rules = active_lines(sections.get("Rule", []))

    header = [
        "#!name=墨鱼去广告模块（Surge兼容版）",
        "#!desc=同步墨鱼原 Modules 持续镜像，并按本仓库规则转换为 Surge 当前兼容语法",
        "#!author=ddgksf2013",
        "#!contributor=@blackmatrix7, @app2smile",
        "#!homepage=https://github.com/ddgksf2013",
        "#!tgchannel=https://t.me/ddgksf2021",
        f"#!raw-url={PUBLIC_RAW_URL}",
        "#!category=去广告",
        "#!requirement=CORE_VERSION>=20",
        "#!remark=由 mrtanshiyue/Amazon 自动维护；上游为原 ddgksf2013/Modules 的持续更新镜像，统一 Surge Rewrite/HTTP Script 语法，脚本名去重，并移除无限 body 缓冲与转换器默认长超时。",
        "",
        f"# Source mirror: {SOURCE}",
        "# Original project: https://github.com/ddgksf2013/Modules",
        "",
    ]

    output = list(header)
    output.extend(["[General]", *general, ""])
    output.extend(["[Rule]", *rules, ""])
    output.extend(["[URL Rewrite]", *url_rewrites, ""])
    output.extend(["[Map Local]", *map_local, ""])
    output.extend(["[Body Rewrite]", ""])
    output.extend(["[Script]", *scripts, ""])
    output.extend(["[MITM]", "", "hostname = %APPEND% " + ",".join(hosts), ""])

    return "\n".join(output).rstrip() + "\n"


def main():
    source = fetch_text(SOURCE)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    existing = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""

    result = convert(source)
    validate_result(result, existing)

    if os.environ.get("SKIP_REMOTE_SCRIPT_CHECK") != "1":
        validate_remote_scripts(result)

    if existing == result:
        print("No upstream or conversion changes.")
        return

    TARGET.write_text(result, encoding="utf-8")
    print(
        f"Updated {TARGET}: "
        f"{section_active_count(result, 'URL Rewrite')} rewrites, "
        f"{section_active_count(result, 'Map Local')} map-local rules, "
        f"{section_active_count(result, 'Script')} scripts, "
        f"{mitm_host_count(result)} MITM hosts"
    )


if __name__ == "__main__":
    main()
