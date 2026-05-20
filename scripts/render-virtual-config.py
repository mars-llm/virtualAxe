#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path


CSV_KEY_MAP = {
    "boardVersion": "boardversion",
    "deviceModel": "devicemodel",
    "asicModel": "asicmodel",
    "powerConsumptionTarget": "power_cons_tgt",
    "temperatureTarget": "temptarget",
}

SENSOR_KEY_MAP = {
    "plugSense": "plug_sense",
    "asicEnable": "asic_enable",
    "EMC2101": "EMC2101",
    "EMC2103": "EMC2103",
    "EMC2302": "EMC2302",
    "emcInternalTemp": "emc_int_temp",
    "emcIdealityFactor": "emc_ideality_f",
    "emcBetaCompensation": "emc_beta_comp",
    "tempOffset": "temp_offset",
    "DS4432U": "DS4432U",
    "INA260": "INA260",
    "TPS546": "TPS546",
    "TMP1075": "TMP1075",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render virtualAxe config-active.csv and sdkconfig overrides.")
    parser.add_argument("--template-csv", required=True)
    parser.add_argument("--profile-json", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-sdkconfig", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--pool-host", required=True)
    parser.add_argument("--pool-port", type=int, required=True)
    parser.add_argument("--pool-user", required=True)
    parser.add_argument("--pool-pass", required=True)
    parser.add_argument("--pool-diff", type=float, required=True)
    parser.add_argument("--pool-tls", type=int, required=True)
    parser.add_argument("--pool-cert", required=True)
    parser.add_argument("--pool-subscribe-agent", default="")
    parser.add_argument("--fallback-pool-host")
    parser.add_argument("--fallback-pool-port", type=int)
    parser.add_argument("--fallback-pool-user")
    parser.add_argument("--fallback-pool-pass")
    parser.add_argument("--fallback-pool-diff", type=float)
    parser.add_argument("--fallback-pool-tls", type=int)
    parser.add_argument("--fallback-pool-cert")
    parser.add_argument("--fallback-pool-subscribe-agent")
    parser.add_argument("--virtual-asic-mode", choices=("cpu",), required=True)
    return parser.parse_args()


def load_csv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [row for row in csv.reader(handle)]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def set_csv_value(rows: list[list[str]], key: str, value: str) -> None:
    for row in rows:
        if row and row[0] == key:
            while len(row) < 4:
                row.append("")
            row[3] = value
            return
    raise KeyError(f"Missing CSV key {key}")


def config_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def fallback_value(value, default):
    return default if value is None else value


def render_float(value: float) -> str:
    return f"{value:.8g}"


def render_sdkconfig_difficulty(value: float) -> int:
    return max(1, int(value))


def render_sdkconfig(args: argparse.Namespace) -> str:
    lines: list[str] = []
    fallback_host = fallback_value(args.fallback_pool_host, args.pool_host)
    fallback_port = fallback_value(args.fallback_pool_port, args.pool_port)
    fallback_user = fallback_value(args.fallback_pool_user, args.pool_user)
    fallback_pass = fallback_value(args.fallback_pool_pass, args.pool_pass)
    fallback_diff = fallback_value(args.fallback_pool_diff, args.pool_diff)
    fallback_tls = fallback_value(args.fallback_pool_tls, args.pool_tls)
    fallback_cert = fallback_value(args.fallback_pool_cert, args.pool_cert)

    lines.append("CONFIG_BITAXE_VIRTUAL_ASIC_MODE_CPU=y")

    lines.append(f'CONFIG_LWIP_LOCAL_HOSTNAME="{config_string(args.hostname)}"')
    lines.append(f'CONFIG_STRATUM_URL="{config_string(args.pool_host)}"')
    lines.append(f"CONFIG_STRATUM_PORT={args.pool_port}")
    lines.append(f'CONFIG_STRATUM_USER="{config_string(args.pool_user)}"')
    lines.append(f'CONFIG_STRATUM_PW="{config_string(args.pool_pass)}"')
    lines.append(f"CONFIG_STRATUM_DIFFICULTY={render_sdkconfig_difficulty(args.pool_diff)}")
    lines.append(f'CONFIG_STRATUM_CERT="{config_string(args.pool_cert)}"')

    lines.append(f'CONFIG_FALLBACK_STRATUM_URL="{config_string(fallback_host)}"')
    lines.append(f"CONFIG_FALLBACK_STRATUM_PORT={fallback_port}")
    lines.append(f'CONFIG_FALLBACK_STRATUM_USER="{config_string(fallback_user)}"')
    lines.append(f'CONFIG_FALLBACK_STRATUM_PW="{config_string(fallback_pass)}"')
    lines.append(f"CONFIG_FALLBACK_STRATUM_DIFFICULTY={render_sdkconfig_difficulty(fallback_diff)}")
    lines.append(f'CONFIG_FALLBACK_STRATUM_CERT="{config_string(fallback_cert)}"')

    tls_settings = {
        "STRATUM_TLS": args.pool_tls,
        "FALLBACK_STRATUM_TLS": fallback_tls,
    }
    for prefix, tls_mode in tls_settings.items():
        states = {
            0: ("DISABLED",),
            1: ("BUNDLED",),
            2: ("CUSTOM",),
        }.get(tls_mode, ("DISABLED",))
        for option in ("DISABLED", "BUNDLED", "CUSTOM"):
            if option in states:
                lines.append(f"CONFIG_{prefix}_{option}=y")
            else:
                lines.append(f"# CONFIG_{prefix}_{option} is not set")

    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    template_csv = Path(args.template_csv)
    profile_json = Path(args.profile_json)
    output_csv = Path(args.output_csv)
    output_sdkconfig = Path(args.output_sdkconfig)
    fallback_host = fallback_value(args.fallback_pool_host, args.pool_host)
    fallback_port = fallback_value(args.fallback_pool_port, args.pool_port)
    fallback_user = fallback_value(args.fallback_pool_user, args.pool_user)
    fallback_pass = fallback_value(args.fallback_pool_pass, args.pool_pass)
    fallback_diff = fallback_value(args.fallback_pool_diff, args.pool_diff)
    fallback_tls = fallback_value(args.fallback_pool_tls, args.pool_tls)
    fallback_cert = fallback_value(args.fallback_pool_cert, args.pool_cert)
    fallback_subscribe_agent = fallback_value(args.fallback_pool_subscribe_agent, args.pool_subscribe_agent)

    profile = json.loads(profile_json.read_text(encoding="utf-8"))
    rows = load_csv(template_csv)

    base_values = {
        "hostname": args.hostname,
        "stratumurl": args.pool_host,
        "stratumport": str(args.pool_port),
        "stratumuser": args.pool_user,
        "stratumpass": args.pool_pass,
        "stratumdiff": render_float(args.pool_diff),
        "stratumtls": str(args.pool_tls),
        "stratumcert": args.pool_cert,
        "stratumagent": args.pool_subscribe_agent,
        "fbstratumurl": fallback_host,
        "fbstratumport": str(fallback_port),
        "fbstratumuser": fallback_user,
        "fbstratumpass": fallback_pass,
        "fbstratumdiff": render_float(fallback_diff),
        "fbstratumtls": str(fallback_tls),
        "fbstratumcert": fallback_cert,
        "fbstratumagent": fallback_subscribe_agent,
    }

    for key, value in base_values.items():
        set_csv_value(rows, key, value)

    for profile_key, csv_key in CSV_KEY_MAP.items():
        if profile_key in profile:
            set_csv_value(rows, csv_key, str(profile[profile_key]))

    sensors = profile.get("sensors", {})
    for profile_key, csv_key in SENSOR_KEY_MAP.items():
        if profile_key not in sensors:
            continue
        value = sensors[profile_key]
        if isinstance(value, bool):
            rendered = "1" if value else "0"
        else:
            rendered = str(value)
        set_csv_value(rows, csv_key, rendered)

    write_csv(output_csv, rows)
    output_sdkconfig.parent.mkdir(parents=True, exist_ok=True)
    output_sdkconfig.write_text(render_sdkconfig(args), encoding="utf-8")


if __name__ == "__main__":
    main()
