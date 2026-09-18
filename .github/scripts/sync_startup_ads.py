#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE = "https://raw.githubusercontent.com/Walvez/surge-startup-ads/main/dist/StartUpAds_Selected.sgmodule"
TARGET = Path("modules/StartUpAds-Surge.sgmodule")
PUBLIC_RAW_URL = "https://raw.githubusercontent.com/mrtanshiyue/Amazon/main/modules/StartUpAds-Surge.sgmodule"
USER_AGENT = "mrtanshiyue-amazon-startup-ads-sync/2.0"

MIN_SOURCE_BYTES = 60_000
SECTION_FLOORS = {
    "Rule": 4,
    "URL Rewrite": 10,
    "Map Local": 300,
    "Body Rewrite": 10,
    "Script": 20,
}
MAX_SECTION_DROP_RATIO = 0.35
MIN_MITM_HOSTS = 300


def fetch_text(url: str, attempts: int = 3, timeout: int = 30) -> str:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8-sig").replace("\r\n", "\n")
        except Exception as error:  # urllib raises several transport/status exception types.
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise SystemExit(f"Failed to fetch {url} after {attempts} attempts: {last_error}")


def split_sections(text: str):
    order = []
    sections = {}
    current = None
    for line in text.split("\n"):
        match = re.match(r"^\[([^\]]+)\]\s*$", line)
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
    """Escape dots only when the URL host expression is a plain literal host/IP.

    Regex hosts containing wildcards, groups, classes, quantifiers or other regex
    operators are intentionally left untouched.
    """
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

    # A plain DNS name or IPv4 address, optionally with a literal numeric port.
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+(?::[0-9]+)?", literal_host):
        return pattern

    escaped_host = re.sub(r"(?<!\\)\.", r"\\.", host_expr)
    return pattern[:host_start] + escaped_host + pattern[host_end:]


def is_version_metadata_rule(pattern: str) -> bool:
    normalized = pattern.replace(r"\/", "/").replace(r"\.", ".")
    match = re.match(r"^\^?https\?://([^/]+)/", normalized)
    if not match:
        return False
    return re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", match.group(1)) is not None


def normalize_map_local_line(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return line
    parts = stripped.split(maxsplit=1)
    if len(parts) != 2:
        raise SystemExit(f"Invalid Map Local line: {line}")
    return f"{normalize_literal_url_host(parts[0])} {parts[1]}"


def encode_body_token(value: str) -> str:
    return value.replace('"', r"\x22").replace(" ", r"\x20")


def parse_body_pairs(rest: str):
    tokens = rest.split()
    if len(tokens) < 2:
        raise SystemExit(f"Body Rewrite requires regex/replacement pairs: {rest}")

    if len(tokens) == 2:
        return [(tokens[0], tokens[1])]

    # Some converted Quantumult X rules contain literal spaces inside one search
    # expression and one replacement expression. Detect a repeated leading token
    # as an unambiguous boundary instead of splitting the token list in half.
    repeated_lead = [index for index in range(1, len(tokens)) if tokens[index] == tokens[0]]
    if len(repeated_lead) == 1:
        boundary = repeated_lead[0]
        left = " ".join(tokens[:boundary])
        right = " ".join(tokens[boundary:])
        return [(left, right)]

    # Native Surge supports consecutive regex/replacement pairs. If every field
    # is already a single whitespace-free token, parse them pair-by-pair.
    if len(tokens) % 2 == 0 and all(token not in {":", "="} for token in tokens):
        return [(tokens[index], tokens[index + 1]) for index in range(0, len(tokens), 2)]

    raise SystemExit(f"Ambiguous Body Rewrite fields; refusing heuristic conversion: {rest}")


def fix_body_rewrite(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return line

    jq = re.match(r"^(http-(?:request|response)-jq)\s+(\S+)\s+(.+)$", stripped)
    if jq:
        direction, pattern, expression = jq.groups()
        return f"{direction} {normalize_literal_url_host(pattern)} {expression}"

    match = re.match(r"^(http-(?:request|response))\s+(\S+)\s+(.+)$", stripped)
    if not match:
        raise SystemExit(f"Unsupported Body Rewrite syntax: {line}")

    direction, pattern, rest = match.groups()
    pattern = normalize_literal_url_host(pattern)
    pairs = parse_body_pairs(rest)

    encoded = []
    for search, replacement in pairs:
        # Tighten the known TestFlight storefront rewrite without changing its meaning.
        if (
            "testflight\\.apple\\.com" in pattern
            and search.startswith('storefrontId"')
            and replacement.startswith('storefrontId"')
        ):
            search = r"storefrontId\x22\s*:\s*\x22.*\x22"
            replacement = r"storefrontId\x22:\x22143441-1,29\x22"
        else:
            search = encode_body_token(search)
            replacement = encode_body_token(replacement)
        encoded.extend([search, replacement])

    return " ".join([direction, pattern, *encoded])


def split_script_params(rhs: str):
    """Split key=value parameters without treating regex commas as delimiters."""
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


def quote_pattern_if_needed(pattern: str) -> str:
    quote = None
    inner = pattern
    if len(pattern) >= 2 and pattern[0] == pattern[-1] and pattern[0] in {'"', "'"}:
        quote = pattern[0]
        inner = pattern[1:-1]

    inner = normalize_literal_url_host(inner)

    if "," in inner:
        escaped = inner.replace("\\", "\\\\").replace('"', r'\"')
        # Undo double escaping of regex backslashes introduced by the generic escape.
        escaped = escaped.replace("\\\\/", "\\/").replace("\\\\.", "\\.").replace("\\\\d", "\\d")
        return f'"{escaped}"'

    if quote:
        return quote + inner + quote
    return inner


def parse_script_line(line: str):
    match = re.match(r"^(.*?)\s*=\s*(.+)$", line.strip())
    if not match:
        raise SystemExit(f"Unsupported Script syntax: {line}")
    name, rhs = match.groups()
    params = split_script_params(rhs)
    return name.strip(), params


def fix_script(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return line

    name, items = parse_script_line(stripped)
    values = dict(items)

    script_type = values.get("type")
    if script_type not in {"http-request", "http-response"}:
        raise SystemExit(f"Unsupported HTTP Script type in {name}: {script_type}")
    if "pattern" not in values or "script-path" not in values:
        raise SystemExit(f"Script missing pattern/script-path: {name}")

    original_max_size = values.get("max-size")
    original_timeout = values.get("timeout")
    converter_defaults = original_max_size == "-1" and original_timeout == "60"

    values["pattern"] = quote_pattern_if_needed(values["pattern"])

    if values.get("requires-body", "").lower() == "false":
        values.pop("requires-body", None)
    if values.get("max-size") == "-1":
        values.pop("max-size", None)
    if values.get("timeout") == "5" or (values.get("timeout") == "60" and converter_defaults):
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

    rendered = ",".join(f"{key}={values[key]}" for key in ordered_keys)
    return f"{name} = {rendered}"


def section_counts(text: str):
    _, sections = split_sections(text)
    return {name: len(active_lines(lines)) for name, lines in sections.items()}


def mitm_host_count(text: str) -> int:
    _, sections = split_sections(text)
    for line in sections.get("MITM", []):
        if line.strip().startswith("hostname ="):
            value = line.split("=", 1)[1].strip()
            if value.startswith("%APPEND%"):
                value = value[len("%APPEND%") :].strip()
            return len([host for host in value.split(",") if host.strip()])
    return 0


def validate_structure(result: str, existing: str):
    required_sections = ["Rule", "URL Rewrite", "Map Local", "Body Rewrite", "Script", "MITM"]
    for section in required_sections:
        if result.count(f"[{section}]") != 1:
            raise SystemExit(f"Unexpected duplicate/missing [{section}] section")

    counts = section_counts(result)
    for section, floor in SECTION_FLOORS.items():
        if counts.get(section, 0) < floor:
            raise SystemExit(
                f"Generated [{section}] unexpectedly small: {counts.get(section, 0)} < {floor}"
            )

    hosts = mitm_host_count(result)
    if hosts < MIN_MITM_HOSTS:
        raise SystemExit(f"MITM hostname list unexpectedly small: {hosts} < {MIN_MITM_HOSTS}")

    if existing:
        old_counts = section_counts(existing)
        for section in SECTION_FLOORS:
            previous = old_counts.get(section, 0)
            current = counts.get(section, 0)
            if previous >= 5 and current < previous * (1 - MAX_SECTION_DROP_RATIO):
                raise SystemExit(
                    f"[{section}] shrank too much ({previous} -> {current}); keeping previous module"
                )
        old_hosts = mitm_host_count(existing)
        if old_hosts >= MIN_MITM_HOSTS and hosts < old_hosts * (1 - MAX_SECTION_DROP_RATIO):
            raise SystemExit(
                f"MITM hostname list shrank too much ({old_hosts} -> {hosts}); keeping previous module"
            )


def validate_generated(result: str):
    required = [
        "#!name=Surge 去开屏模块（兼容版）",
        "#!requirement=CORE_VERSION>=20",
        f"#!raw-url={PUBLIC_RAW_URL}",
        "hostname = %APPEND%",
    ]
    for item in required:
        if item not in result:
            raise SystemExit(f"Missing required content: {item}")

    if len(result) < 60_000:
        raise SystemExit("Generated module unexpectedly short")
    if re.search(r"\s-\sreject(?:-[A-Za-z0-9_-]+)?\s*$", result, re.MULTILINE):
        raise SystemExit("Legacy QX reject syntax survived conversion")
    if re.search(r"^https\?:\\/\\/\d{4}\.\d{2}\.\d{2}/", result, re.MULTILINE):
        raise SystemExit("Version metadata pseudo-rule survived conversion")
    if "max-size=-1" in result:
        raise SystemExit("Unlimited HTTP Script body size survived conversion")
    if "timeout=60" in result:
        raise SystemExit("Converter-default Script timeout survived conversion")

    _, sections = split_sections(result)

    seen = {}
    for section in ["Rule", "URL Rewrite", "Map Local", "Body Rewrite", "Script"]:
        for line in active_lines(sections.get(section, [])):
            key = (section, line)
            if key in seen:
                raise SystemExit(f"Duplicate generated line in [{section}]: {line}")
            seen[key] = True

    for line in active_lines(sections.get("Script", [])):
        name, params = parse_script_line(line)
        values = dict(params)
        if values.get("type") not in {"http-request", "http-response"}:
            raise SystemExit(f"Unexpected Script type: {line}")
        if "pattern" not in values or "script-path" not in values:
            raise SystemExit(f"Generated Script missing required parameters: {line}")
        pattern = values["pattern"]
        inner = pattern[1:-1] if len(pattern) >= 2 and pattern[0] == pattern[-1] == '"' else pattern
        if "," in inner and not (pattern.startswith('"') and pattern.endswith('"')):
            raise SystemExit(f"Comma-bearing Script pattern is not quoted: {line}")


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
                sample = response.read(2048)
                if not sample:
                    raise ValueError("empty response")
                return url
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"{url}: {last_error}")


def validate_remote_scripts(result: str):
    urls = script_urls(result)
    if len(urls) < 15:
        raise SystemExit(f"Unexpectedly few remote scripts: {len(urls)}")

    failures = []
    workers = min(8, len(urls))
    with ThreadPoolExecutor(max_workers=workers) as executor:
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


def convert(source: str, existing: str) -> str:
    if len(source) < MIN_SOURCE_BYTES:
        raise SystemExit("Unexpectedly short upstream module; keeping existing file")
    if "#!name=Surge 去开屏模块" not in source:
        raise SystemExit("Unexpected upstream module header")
    if "[URL Rewrite]" not in source or "[MITM]" not in source:
        raise SystemExit("Required upstream sections missing")

    order, sections = split_sections(source)

    url_rewrite = []
    converted_map_local = []
    pending = []

    for raw in sections.get("URL Rewrite", []):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            pending.append(raw)
            continue

        match = re.match(
            r"^(.*?)\s+-\s+(reject(?:-[A-Za-z0-9_-]+)?)\s*$",
            raw,
            re.IGNORECASE,
        )
        if not match:
            raise SystemExit(f"Unsupported URL Rewrite syntax: {raw}")

        pattern, action = match.groups()
        pattern = pattern.strip()
        action = action.lower()

        if is_version_metadata_rule(pattern):
            pending = []
            print(f"Filtered upstream version metadata pseudo-rule: {pattern}")
            continue

        pattern = normalize_literal_url_host(pattern)

        destination = url_rewrite if action == "reject" else converted_map_local
        destination.extend(pending)
        pending = []

        if action == "reject":
            destination.append(f"{pattern} _ reject")
        elif action == "reject-200":
            destination.append(f'{pattern} data-type=text data="" status-code=200')
        elif action == "reject-dict":
            destination.append(
                f'{pattern} data-type=text data="{{}}" status-code=200 '
                'header="Content-Type:application/json"'
            )
        elif action == "reject-img":
            destination.append(f"{pattern} data-type=tiny-gif status-code=200")
        else:
            raise SystemExit(f"Unsupported URL Rewrite action: {action}")

    url_rewrite.extend(pending)
    sections["URL Rewrite"] = url_rewrite
    sections["Map Local"] = converted_map_local + [
        normalize_map_local_line(line) for line in sections.get("Map Local", [])
    ]
    sections["Body Rewrite"] = [fix_body_rewrite(line) for line in sections.get("Body Rewrite", [])]
    sections["Script"] = [fix_script(line) for line in sections.get("Script", [])]

    header = [
        "#!name=Surge 去开屏模块（兼容版）",
        "#!desc=每日同步 Walvez 上游，并自动转换为 Surge 当前兼容语法",
        "#!author=ddgksf2013 + Walvez",
        "#!homepage=https://github.com/Walvez/surge-startup-ads",
        f"#!raw-url={PUBLIC_RAW_URL}",
        "#!category=去广告",
        "#!requirement=CORE_VERSION>=20",
        "#!remark=由 mrtanshiyue/Amazon 自动维护；过滤上游元数据伪规则，规范 URL host regex，并按 Surge 当前语法结构化转换 Body Rewrite / HTTP Script。",
        "",
        f"# Source: {SOURCE}",
        "",
    ]

    output = list(header)
    for section in order:
        output.append(f"[{section}]")
        output.extend(sections[section])
        output.append("")

    result = "\n".join(output).rstrip() + "\n"
    validate_generated(result)
    validate_structure(result, existing)
    return result


def main():
    source = fetch_text(SOURCE)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    existing = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""

    converted = convert(source, existing)
    if os.environ.get("SKIP_REMOTE_SCRIPT_CHECK") != "1":
        validate_remote_scripts(converted)

    if existing == converted:
        print("No upstream or conversion changes.")
        return

    TARGET.write_text(converted, encoding="utf-8")
    print(f"Updated {TARGET}")


if __name__ == "__main__":
    main()
