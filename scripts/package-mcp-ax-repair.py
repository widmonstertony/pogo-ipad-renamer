"""Replace only the MCP server in the already-installed, portable rootless package."""
import gzip
import hashlib
import io
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
base = root / ".pogo-data/mcp-patches/ios-mcp_1.2.5-stage-manager3_iphoneos-arm64.deb"
dylib = Path("/private/tmp/ios-mcp-source/.theos/obj/ios-mcp.dylib")
output = root / ".pogo-data/mcp-patches/ios-mcp_1.2.5-stage-manager4-axfix1_iphoneos-arm64.deb"
ar = "/opt/homebrew/opt/binutils/bin/ar"
replacement = "var/jb/Library/MobileSubstrate/DynamicLibraries/ios-mcp.dylib"
binary = dylib.read_bytes()
assert b"1.2.5-axfix1" in binary
assert "arm64" in subprocess.check_output(["file", str(dylib)], text=True)
subprocess.run(["codesign", "--verify", str(dylib)], check=True)
expected = {}

def repack(member):
    payload = member == "data.tar.gz"
    raw = subprocess.check_output([ar, "p", str(base), member])
    target_buffer = io.BytesIO()
    found = False
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as source:
        with tarfile.open(fileobj=target_buffer, mode="w", format=tarfile.USTAR_FORMAT) as target:
            for old in source:
                name = old.name.removeprefix("./").rstrip("/")
                parts = PurePosixPath(name).parts
                assert ".." not in parts and not name.startswith("/")
                assert not any(p.startswith("._") or p == ".DS_Store" for p in parts)
                assert old.isfile() or old.isdir()
                if payload:
                    assert name == "var/jb" or name.startswith("var/jb/")
                data = source.extractfile(old).read() if old.isfile() else b""
                if payload and name == replacement:
                    data = binary
                    found = True
                elif not payload and name == "control":
                    lines = data.decode().splitlines()
                    assert "Architecture: iphoneos-arm64" in lines
                    assert "Package: com.witchan.ios-mcp" in lines
                    lines = [line for line in lines if not line.startswith("Version:")]
                    lines.append("Version: 1.2.5-stage-manager4-axfix1")
                    data = ("\n".join(lines) + "\n").encode()
                item = tarfile.TarInfo(name)
                item.mode, item.type = old.mode, old.type
                item.uid = item.gid = 0
                item.uname = item.gname = "root"
                item.size = len(data)
                target.addfile(item, io.BytesIO(data) if old.isfile() else None)
                if payload and old.isfile():
                    expected[name] = hashlib.sha256(data).hexdigest()
    if payload:
        assert found
    return gzip.compress(target_buffer.getvalue(), mtime=0)

members = [("debian-binary", b"2.0\n"), ("control.tar.gz", repack("control.tar.gz")), ("data.tar.gz", repack("data.tar.gz"))]
archive = bytearray(b"!<arch>\n")
for name, data in members:
    header = f'{name + "/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(data):<10}`\n'.encode()
    assert len(header) == 60
    archive.extend(header + data + (b"\n" if len(data) % 2 else b""))
with output.open("xb") as handle:
    handle.write(archive)

actual = {}
with tarfile.open(fileobj=io.BytesIO(subprocess.check_output([ar, "p", str(output), "data.tar.gz"])), mode="r:gz") as check:
    for item in check:
        assert not item.pax_headers
        if item.isfile():
            actual[item.name] = hashlib.sha256(check.extractfile(item).read()).hexdigest()
assert actual == expected
print("Verified portable rootless package; only server dylib and version changed.")
print("SHA256", hashlib.sha256(archive).hexdigest())
print(output)
