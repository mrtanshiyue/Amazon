#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen

PRIMARY_SOURCE = "https://ddgksf2013.top/rewrite/StartUpAds.conf"
FALLBACK_SOURCE = "https://raw.githubusercontent.com/master-zen/Net-Link/main/Surge/Module/upstream/StartUpAds.conf"
TARGET = Path("modules/StartUpAds-Surge.sgmodule")
PUBLIC_RAW_URL = "https://raw.githubusercontent.com/mrtanshiyue/Amazon/main/modules/StartUpAds-Surge.sgmodule"
USER_AGENT = "mrtanshiyue-amazon-moyu-startup-sync/5.0"

MIN_SOURCE_BYTES = 40_000
MIN_APP_LABELS = 350
MIN_URL_REWRITES = 10
MIN_MAP_LOCAL = 400
MIN_BODY_REWRITE = 10
MIN_SCRIPTS = 20
MIN_MITM_HOSTS = 400
MAX_DROP_RATIO = 0.35


def fetch_text(url: str, attempts: int = 3, timeout: int = 30) -> str:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/plain,*/*;q=0.8",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8-sig").replace("\r\n", "\n")
            if not text.strip():
                raise ValueError("empty response")
            return text
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"{url}: {last_error}")


def valid_source(text: str) -> bool:
    head = text[:2048].lower()
    return (
        len(text.encode("utf-8")) >= MIN_SOURCE_BYTES
        and "墨鱼去开屏" in text[:4096]
        and "<html" not in head
        and "<!doctype html" not in head
        and " url " in text
    )


def fetch_source() -> tuple[str, str]:
    errors = []
    for url in (PRIMARY_SOURCE, FALLBACK_SOURCE):
        try:
            text = fetch_text(url)
            if not valid_source(text):
                raise ValueError("response is not a valid StartUpAds.conf")
            print(f"Using source: {url}")
            return url, text
        except Exception as error:
            errors.append(f"{url}: {error}")
            print(f"Source unavailable/invalid, trying fallback: {url}: {error}")
    raise SystemExit("Unable to fetch valid MoYu StartUpAds source:\n" + "\n".join(errors))


def app_labels(text: str) -> list[str]:
    labels = []
    for line in text.splitlines():
        match = re.match(r"^#\s*>\s*(.+?)\s*$", line.strip())
        if match and match.group(1).strip().lower() != "version":
            labels.append(match.group(1).strip())
    return labels


def source_update_time(text: str) -> str:
    match = re.search(r"^//\s*@UpdateTime\s+(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else "unknown"


def current_label_from_comment(line: str, current: str) -> str:
    match = re.match(r"^#\s*>\s*(.+?)\s*$", line)
    if not match:
        return current
    value = match.group(1).strip()
    return current if value.lower() == "version" else value


def is_version_sentinel(pattern: str) -> bool:
    normalized = pattern.replace(r"\/", "/")
    return bool(re.match(r"^\^https\?://20\d{2}\.\d{2}\.\d{2}/", normalized))


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


def quote_script_pattern(pattern: str) -> str:
    pattern = normalize_literal_url_host(pattern)
    if "," not in pattern:
        return pattern
    if '"' in pattern:
        raise SystemExit(f'Cannot safely quote comma-bearing Script pattern: {pattern}')
    return f'"{pattern}"'


def encode_body_token(value: str) -> str:
    return value.replace('"', r"\x22").replace(" ", r"\x20")


def render_grouped(items: list[tuple[str, str]]) -> list[str]:
    output = []
    previous = None
    for label, line in items:
        if label and label != previous:
            if output:
                output.append("")
            output.append(f"# > {label}")
            previous = label
        output.append(line)
    return output


def dedupe_grouped(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen = set()
    output = []
    for label, line in items:
        if line in seen:
            continue
        seen.add(line)
        output.append((label, line))
    return output


def parse(text: str):
    rules: list[tuple[str, str]] = []
    url_rewrite: list[tuple[str, str]] = []
    map_local: list[tuple[str, str]] = []
    body_rewrite: list[tuple[str, str]] = []
    scripts: list[tuple[str, str]] = []
    hosts: list[str] = []

    current_label = ""
    script_index = 0
    active_rules = 0

    script_types = {
        "script-response-body": ("http-response", True),
        "script-response-header": ("http-response", False),
        "script-request-body": ("http-request", True),
        "script-request-header": ("http-request", False),
        "script-analyze-echo-response": ("http-request", True),
        "script-echo-response": ("http-request", False),
    }

    for line_no, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line:
            continue

        if line.startswith("#"):
            current_label = current_label_from_comment(line, current_label)
            continue
        if line.startswith((";", "//")):
            continue

        hostname_match = re.match(r"^hostname\s*=\s*(.+)$", line, re.IGNORECASE)
        if hostname_match:
            hosts.extend(
                host.strip()
                for host in hostname_match.group(1).split(",")
                if host.strip()
            )
            continue

        filter_match = re.match(
            r"^(host|host-suffix|host-keyword|host-wildcard)\s*,\s*([^,]+?)\s*,\s*([^,\s]+)\s*$",
            line,
            re.IGNORECASE,
        )
        if filter_match:
            qx_type, value, policy = filter_match.groups()
            type_map = {
                "host": "DOMAIN",
                "host-suffix": "DOMAIN-SUFFIX",
                "host-keyword": "DOMAIN-KEYWORD",
                "host-wildcard": "DOMAIN-WILDCARD",
            }
            policy_map = {"direct": "DIRECT", "reject": "REJECT"}
            policy_key = policy.lower()
            if policy_key not in policy_map:
                raise SystemExit(f"Unsupported filter policy at line {line_no}: {original}")
            rules.append(
                (
                    current_label,
                    f"{type_map[qx_type.lower()]},{value.strip()},{policy_map[policy_key]}",
                )
            )
            active_rules += 1
            continue

        if re.match(r"^(ip-cidr|ip6-cidr|geoip|final)\s*,", line, re.IGNORECASE):
            raise SystemExit(f"Global routing rule is not allowed in ad module at line {line_no}: {original}")

        if " url " not in line:
            raise SystemExit(f"Unsupported StartUpAds syntax at line {line_no}: {original}")

        pattern, action = line.split(" url ", 1)
        pattern = normalize_literal_url_host(pattern.strip())
        action = action.strip()

        if is_version_sentinel(pattern):
            continue

        active_rules += 1

        if action == "reject":
            url_rewrite.append((current_label, f"{pattern} _ reject"))
            continue

        if action == "reject-200":
            map_local.append((current_label, f'{pattern} data-type=text data="" status-code=200'))
            continue

        if action == "reject-img":
            map_local.append((current_label, f"{pattern} data-type=tiny-gif status-code=200"))
            continue

        if action == "reject-dict":
            map_local.append(
                (
                    current_label,
                    f'{pattern} data-type=text data="{{}}" status-code=200 '
                    'header="Content-Type:application/json;charset=utf-8"',
                )
            )
            continue

        if action == "reject-array":
            map_local.append(
                (
                    current_label,
                    f'{pattern} data-type=text data="[]" status-code=200 '
                    'header="Content-Type:application/json;charset=utf-8"',
                )
            )
            continue

        if action.startswith(("302 ", "307 ")):
            status, target = action.split(maxsplit=1)
            if not target.startswith(("http://", "https://")):
                raise SystemExit(f"Invalid redirect target at line {line_no}: {original}")
            url_rewrite.append((current_label, f"{pattern} {target} {status}"))
            continue

        if action.startswith("response-body "):
            rest = action[len("response-body "):]
            separator = " response-body "
            if separator not in rest:
                raise SystemExit(f"Invalid response-body at line {line_no}: {original}")
            find, replacement = rest.split(separator, 1)
            body_rewrite.append(
                (
                    current_label,
                    f"http-response {pattern} {encode_body_token(find)} {encode_body_token(replacement)}",
                )
            )
            continue

        if action.startswith("request-body "):
            rest = action[len("request-body "):]
            separator = " request-body "
            if separator not in rest:
                raise SystemExit(f"Invalid request-body at line {line_no}: {original}")
            find, replacement = rest.split(separator, 1)
            body_rewrite.append(
                (
                    current_label,
                    f"http-request {pattern} {encode_body_token(find)} {encode_body_token(replacement)}",
                )
            )
            continue

        if action.startswith("jsonjq-response-body "):
            jq = action[len("jsonjq-response-body "):].strip()
            if not jq:
                raise SystemExit(f"Empty jsonjq-response-body at line {line_no}")
            body_rewrite.append((current_label, f"http-response-jq {pattern} {jq}"))
            continue

        if action.startswith("jsonjq-request-body "):
            jq = action[len("jsonjq-request-body "):].strip()
            if not jq:
                raise SystemExit(f"Empty jsonjq-request-body at line {line_no}")
            body_rewrite.append((current_label, f"http-request-jq {pattern} {jq}"))
            continue

        if action.startswith("echo-response "):
            rest = action[len("echo-response "):]
            separator = " echo-response "
            if separator not in rest:
                raise SystemExit(f"Invalid echo-response at line {line_no}: {original}")
            response_meta, resource = rest.split(separator, 1)
            response_meta = response_meta.strip()
            resource = resource.strip()
            if not resource.startswith("https://"):
                raise SystemExit(f"Non-HTTPS echo resource at line {line_no}: {resource}")
            parts = response_meta.split(r"\r\n")
            content_type = parts[0].strip()
            headers = [f"Content-Type:{content_type}"]
            for extra in parts[1:]:
                extra = extra.strip()
                if extra:
                    if ":" not in extra:
                        raise SystemExit(f"Invalid echo-response header at line {line_no}: {extra}")
                    headers.append(extra)
            header_value = "|".join(headers)
            map_local.append(
                (
                    current_label,
                    f'{pattern} data-type=file data="{resource}" '
                    f'header="{header_value}" status-code=200',
                )
            )
            continue

        handled = False
        for qx_type, (surge_type, requires_body) in script_types.items():
            prefix = qx_type + " "
            if not action.startswith(prefix):
                continue
            script_path = action[len(prefix):].strip()
            if not script_path.startswith("https://"):
                raise SystemExit(f"Non-HTTPS Script URL at line {line_no}: {script_path}")
            script_index += 1
            params = [
                f"type={surge_type}",
                f"pattern={quote_script_pattern(pattern)}",
                f"script-path={script_path}",
            ]
            if requires_body:
                params.append("requires-body=true")
            scripts.append(
                (
                    current_label,
                    f"Moyu_{script_index:04d} = " + ",".join(params),
                )
            )
            handled = True
            break

        if handled:
            continue

        raise SystemExit(
            "New/unsupported Quantumult X action; refusing to publish\n"
            f"line {line_no}: {original}"
        )

    return {
        "rules": dedupe_grouped(rules),
        "url_rewrite": dedupe_grouped(url_rewrite),
        "map_local": dedupe_grouped(map_local),
        "body_rewrite": dedupe_grouped(body_rewrite),
        "scripts": dedupe_grouped(scripts),
        "hosts": sorted(set(hosts), key=str.lower),
        "active_rules": active_rules,
    }


def section_count(text: str, section: str) -> int:
    marker = f"[{section}]"
    start = text.find(marker)
    if start < 0:
        return 0
    tail = text[start + len(marker):]
    next_match = re.search(r"^\[[^\]]+\]\s*$", tail, re.MULTILINE)
    body = tail[: next_match.start()] if next_match else tail
    return len(
        [
            line
            for line in body.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    )


def mitm_count(text: str) -> int:
    match = re.search(r"^hostname\s*=\s*%APPEND%\s+(.+)$", text, re.MULTILINE)
    if not match:
        return 0
    return len([item for item in match.group(1).split(",") if item.strip()])


def validate(result: str, existing: str, label_count: int):
    required_sections = ["Rule", "URL Rewrite", "Map Local", "Body Rewrite", "Script", "MITM"]
    for section in required_sections:
        if result.count(f"[{section}]") != 1:
            raise SystemExit(f"Missing/duplicate [{section}]")

    floors = {
        "URL Rewrite": MIN_URL_REWRITES,
        "Map Local": MIN_MAP_LOCAL,
        "Body Rewrite": MIN_BODY_REWRITE,
        "Script": MIN_SCRIPTS,
    }
    for section, floor in floors.items():
        count = section_count(result, section)
        if count < floor:
            raise SystemExit(f"[{section}] unexpectedly small: {count} < {floor}")

    if label_count < MIN_APP_LABELS:
        raise SystemExit(f"Too few app labels: {label_count} < {MIN_APP_LABELS}")

    hosts = mitm_count(result)
    if hosts < MIN_MITM_HOSTS:
        raise SystemExit(f"Too few MITM hosts: {hosts} < {MIN_MITM_HOSTS}")

    if " url " in result:
        raise SystemExit("Quantumult X rewrite syntax survived conversion")
    if "max-size=-1" in result or "timeout=60" in result:
        raise SystemExit("Unsafe/default script parameters survived conversion")

    if existing and "#!name=墨鱼去开屏2.0（Surge兼容版）" in existing:
        for section in floors:
            old = section_count(existing, section)
            new = section_count(result, section)
            if old >= 10 and new < old * (1 - MAX_DROP_RATIO):
                raise SystemExit(f"[{section}] shrank too much ({old} -> {new})")
        old_hosts = mitm_count(existing)
        if old_hosts >= MIN_MITM_HOSTS and hosts < old_hosts * (1 - MAX_DROP_RATIO):
            raise SystemExit(f"MITM hosts shrank too much ({old_hosts} -> {hosts})")


def script_urls(result: str) -> list[str]:
    match = re.search(r"\[Script\]([\s\S]*?)\n\[MITM\]", result)
    if not match:
        return []
    return sorted(
        set(
            re.findall(
                r"script-path=(https?://[^,\s]+)",
                match.group(1),
            )
        )
    )


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
        raise SystemExit(f"Unexpectedly few remote scripts: {len(urls)}")

    failures = []
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as executor:
        futures = {executor.submit(check_remote_script, url): url for url in urls}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:
                failures.append(str(error))

    if failures:
        raise SystemExit(
            "Remote Script health check failed; keeping previous module:\n"
            + "\n".join(f"  - {failure}" for failure in sorted(failures))
        )

    print(f"Remote Script health check passed for {len(urls)} unique URLs.")


def build(source_url: str, source: str) -> tuple[str, int]:
    parsed = parse(source)
    labels = app_labels(source)
    unique_labels = len(set(labels))
    update_time = source_update_time(source)

    header = [
        "#!name=墨鱼去开屏2.0（Surge兼容版）",
        f"#!desc=同步墨鱼 StartUpAds.conf；当前约 {unique_labels} 个唯一应用/规则标签",
        "#!author=ddgksf2013",
        "#!homepage=https://github.com/ddgksf2013",
        "#!tgchannel=https://t.me/ddgksf2021",
        f"#!raw-url={PUBLIC_RAW_URL}",
        "#!category=去广告",
        "#!requirement=CORE_VERSION>=20",
        f"#!remark=上游更新时间 {update_time}；由 mrtanshiyue/Amazon 自动同步并转换为 Surge 语法。",
        "",
        f"# Primary source: {PRIMARY_SOURCE}",
        f"# Fetched from: {source_url}",
        f"# App/rule labels: {len(labels)} total / {unique_labels} unique",
        "",
    ]

    output = list(header)
    output.append("[Rule]")
    output.extend(render_grouped(parsed["rules"]))
    output.append("")

    output.append("[URL Rewrite]")
    output.extend(render_grouped(parsed["url_rewrite"]))
    output.append("")

    output.append("[Map Local]")
    output.extend(render_grouped(parsed["map_local"]))
    output.append("")

    output.append("[Body Rewrite]")
    output.extend(render_grouped(parsed["body_rewrite"]))
    output.append("")

    output.append("[Script]")
    output.extend(render_grouped(parsed["scripts"]))
    output.append("")

    output.extend(["[MITM]", "", "hostname = %APPEND% " + ",".join(parsed["hosts"]), ""])

    result = "\n".join(output).rstrip() + "\n"
    return result, unique_labels


def main():
    source_url, source = fetch_source()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    existing = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""

    result, unique_labels = build(source_url, source)
    validate(result, existing, unique_labels)

    if os.environ.get("SKIP_REMOTE_SCRIPT_CHECK") != "1":
        validate_remote_scripts(result)

    if result == existing:
        print("No upstream or conversion changes.")
        return

    TARGET.write_text(result, encoding="utf-8")
    print(
        f"Updated {TARGET}: "
        f"{unique_labels} unique app/rule labels, "
        f"{section_count(result, 'Rule')} rules, "
        f"{section_count(result, 'URL Rewrite')} URL rewrites, "
        f"{section_count(result, 'Map Local')} map-local rules, "
        f"{section_count(result, 'Body Rewrite')} body rewrites, "
        f"{section_count(result, 'Script')} scripts, "
        f"{mitm_count(result)} MITM hosts"
    )


if __name__ == "__main__":
    main()
