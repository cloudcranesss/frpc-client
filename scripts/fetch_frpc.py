from __future__ import annotations

import argparse
import asyncio
import io
import json
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO_API = "https://api.github.com/repos/fatedier/frp/releases"


class DownloadError(RuntimeError):
    pass


async def fetch_json(url: str) -> dict:
    return await asyncio.to_thread(_fetch_json_sync, url)


def _fetch_json_sync(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "frp-web-client-fetcher",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def download_bytes(url: str) -> bytes:
    return await asyncio.to_thread(_download_bytes_sync, url)


def _download_bytes_sync(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "frp-web-client-fetcher"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


async def resolve_release(version: str | None) -> dict:
    if version:
        version = version.strip()
        if not version.startswith("v"):
            version = f"v{version}"
        return await fetch_json(f"{REPO_API}/tags/{version}")
    return await fetch_json(f"{REPO_API}/latest")


def pick_asset(assets: list[dict], suffix: str) -> dict:
    for asset in assets:
        name = str(asset.get("name", ""))
        if name.endswith(suffix):
            return asset
    raise DownloadError(f"Cannot find asset with suffix: {suffix}")


async def extract_windows_frpc(archive_data: bytes, output_path: Path) -> None:
    await asyncio.to_thread(_extract_windows_frpc_sync, archive_data, output_path)


def _extract_windows_frpc_sync(archive_data: bytes, output_path: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        frpc_entry = next(
            (name for name in archive.namelist() if name.endswith("/frpc.exe")),
            None,
        )
        if not frpc_entry:
            raise DownloadError("frpc.exe not found in Windows archive.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(archive.read(frpc_entry))


async def extract_linux_frpc(archive_data: bytes, output_path: Path) -> None:
    await asyncio.to_thread(_extract_linux_frpc_sync, archive_data, output_path)


def _extract_linux_frpc_sync(archive_data: bytes, output_path: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as archive:
        member = next((m for m in archive.getmembers() if m.name.endswith("/frpc")), None)
        if not member:
            raise DownloadError("frpc not found in Linux archive.")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise DownloadError("Cannot extract frpc from Linux archive.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(extracted.read())
        output_path.chmod(0o755)


async def write_text(path: Path, content: str) -> None:
    await asyncio.to_thread(path.write_text, content, "utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Download frpc for Windows and Linux.")
    parser.add_argument(
        "--version",
        default=None,
        help="frp version (e.g. 0.61.1 or v0.61.1). Default uses latest release.",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    bin_dir = project_root / "bin"
    windows_output = bin_dir / "windows" / "frpc.exe"
    linux_output = bin_dir / "linux" / "frpc"

    release = await resolve_release(args.version)
    tag_name = str(release.get("tag_name", "")).strip()
    if not tag_name:
        raise DownloadError("Invalid release data: missing tag_name.")
    version = tag_name.lstrip("v")
    assets: list[dict] = list(release.get("assets") or [])

    windows_suffix = f"frp_{version}_windows_amd64.zip"
    linux_suffix = f"frp_{version}_linux_amd64.tar.gz"
    windows_asset = pick_asset(assets, windows_suffix)
    linux_asset = pick_asset(assets, linux_suffix)

    print(f"Using FRP release: {tag_name}")
    print(f"Downloading: {windows_asset['name']}")
    windows_data, linux_data = await asyncio.gather(
        download_bytes(str(windows_asset["browser_download_url"])),
        download_bytes(str(linux_asset["browser_download_url"])),
    )

    print(f"Downloading: {linux_asset['name']}")
    await asyncio.gather(
        extract_windows_frpc(windows_data, windows_output),
        extract_linux_frpc(linux_data, linux_output),
    )

    metadata = (
        f"tag={tag_name}\n"
        f"windows_asset={windows_asset['name']}\n"
        f"linux_asset={linux_asset['name']}\n"
    )
    await write_text(bin_dir / "FRP_VERSION.txt", metadata)

    print(f"Saved: {windows_output}")
    print(f"Saved: {linux_output}")
    print(f"Saved: {bin_dir / 'FRP_VERSION.txt'}")


if __name__ == "__main__":
    asyncio.run(main())

