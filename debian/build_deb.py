#!/usr/bin/env python3
"""Portable Debian Package Creator for Sysmon Agent.

Allows building the .deb package directly on Windows or Linux without requiring dpkg-deb.
Creates a valid Debian ar archive containing control.tar.gz and data.tar.gz.
"""

import os
import tarfile
import time
import io
from pathlib import Path

DEBIAN_BINARY = b"2.0\n"

def create_tar_gz(entries):
    """Create a gzipped tar archive from a dictionary of entries.
    
    entries is a dict: { arcname: (source_path_or_bytes_or_none, mode) }
    """
    tar_io = io.BytesIO()
    # Explicitly use gzip compression
    with tarfile.open(fileobj=tar_io, mode='w:gz', format=tarfile.GNU_FORMAT) as tar:
        for arcname, (source, mode) in entries.items():
            if source is None:
                # Directory entry
                tarinfo = tarfile.TarInfo(name=arcname)
                tarinfo.type = tarfile.DIRTYPE
                tarinfo.mode = mode
                tarinfo.mtime = int(time.time())
                tarinfo.uid = 0
                tarinfo.gid = 0
                tarinfo.uname = "root"
                tarinfo.gname = "root"
                tar.addfile(tarinfo)
            elif isinstance(source, bytes):
                tarinfo = tarfile.TarInfo(name=arcname)
                tarinfo.size = len(source)
                tarinfo.mode = mode
                tarinfo.mtime = int(time.time())
                tarinfo.uid = 0
                tarinfo.gid = 0
                tarinfo.uname = "root"
                tarinfo.gname = "root"
                tar.addfile(tarinfo, io.BytesIO(source))
            else:
                source_path = Path(source)
                tarinfo = tarfile.TarInfo(name=arcname)
                tarinfo.size = source_path.stat().st_size
                tarinfo.mode = mode
                tarinfo.mtime = int(source_path.stat().st_mtime)
                tarinfo.uid = 0
                tarinfo.gid = 0
                tarinfo.uname = "root"
                tarinfo.gname = "root"
                with open(source_path, 'rb') as f:
                    tar.addfile(tarinfo, f)
    return tar_io.getvalue()

def build_ar_archive(entries):
    """Build a standard ar archive from a list of (filename, data_bytes) tuples."""
    ar_data = b"!<arch>\n"
    for filename, data in entries:
        size = len(data)
        # 60-byte header format:
        # name(16), mtime(12), owner(6), group(6), mode(8), size(10), magic(2)
        header = f"{filename:<16}{'0':<12}{'0':<6}{'0':<6}{'100644':<8}{size:<10}`\n".encode('ascii')
        ar_data += header + data
        # Pad with a newline if the data size is odd
        if size % 2 == 1:
            ar_data += b"\n"
    return ar_data

def main():
    debian_dir = Path(__file__).parent
    project_dir = debian_dir.parent
    
    print("=" * 60)
    print("Building Sysmon Agent Debian Package (.deb) Portably...")
    print("=" * 60)
    
    # 1. Prepare control.tar.gz
    print(">>> Creating control.tar.gz...")
    control_entries = {}
    control_src = debian_dir / "DEBIAN" / "control"
    postinst_src = debian_dir / "DEBIAN" / "postinst"
    prerm_src = debian_dir / "DEBIAN" / "prerm"
    
    if not control_src.exists():
        print(f"Error: {control_src} does not exist.")
        return
        
    control_entries["./control"] = (control_src, 0o644)
    if postinst_src.exists():
        control_entries["./postinst"] = (postinst_src, 0o755)
    if prerm_src.exists():
        control_entries["./prerm"] = (prerm_src, 0o755)
        
    control_tar = create_tar_gz(control_entries)
    
    # 2. Prepare data.tar.gz
    print(">>> Creating data.tar.gz...")
    data_entries = {}
    
    agent_sh = project_dir / "agents" / "linux" / "agent.sh"
    config_json = project_dir / "agents" / "linux" / "config.json"
    service_file = project_dir / "deployment" / "sysmon-agent.service"
    
    if not agent_sh.exists() or not config_json.exists() or not service_file.exists():
        print("Error: Missing agent files or systemd service file.")
        return
        
    # Explicitly define directories first so dpkg can extract files properly
    data_entries["./opt"] = (None, 0o755)
    data_entries["./opt/sysmon-agent"] = (None, 0o755)
    data_entries["./lib"] = (None, 0o755)
    data_entries["./lib/systemd"] = (None, 0o755)
    data_entries["./lib/systemd/system"] = (None, 0o755)
    
    # Add files
    data_entries["./opt/sysmon-agent/agent.sh"] = (agent_sh, 0o755)
    data_entries["./opt/sysmon-agent/config.json"] = (config_json, 0o644)
    data_entries["./lib/systemd/system/sysmon-agent.service"] = (service_file, 0o644)
    
    data_tar = create_tar_gz(data_entries)
    
    # 3. Assemble .deb
    print(">>> Assembling ar archive (.deb)...")
    ar_entries = [
        ("debian-binary", DEBIAN_BINARY),
        ("control.tar.gz", control_tar),
        ("data.tar.gz", data_tar)
    ]
    deb_data = build_ar_archive(ar_entries)
    
    output_filename = "sysmon-agent_2.0.0_all.deb"
    output_path = debian_dir / output_filename
    with open(output_path, 'wb') as f:
        f.write(deb_data)
        
    print("=" * 60)
    print("Success! Package created at:")
    print(f"   {output_path.absolute()}")
    print(f"   Size: {output_path.stat().st_size} bytes (~{output_path.stat().st_size / 1024:.1f} KB)")
    print("=" * 60)

if __name__ == "__main__":
    main()
