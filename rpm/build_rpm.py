#!/usr/bin/env python3
"""Portable RPM Package Creator for Sysmon Agent.

Creates a source tarball and optionally builds the .rpm package
using rpmbuild if available. Works on both Linux and Windows.

Usage:
    python rpm/build_rpm.py

On Linux with rpmbuild installed:
    - Automatically builds the .rpm package.

On Windows or Linux without rpmbuild:
    - Creates a source tarball ready for rpmbuild.
    - Prints instructions for building on a Linux machine.
"""

import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


VERSION = "2.0.0"
PKG_NAME = "sysmon-agent"


def main():
    rpm_dir = Path(__file__).parent
    project_dir = rpm_dir.parent

    print("=" * 60)
    print("Building Sysmon Agent RPM Package...")
    print("=" * 60)

    # Verify required source files exist
    agent_sh = project_dir / "agents" / "linux" / "agent.sh"
    config_json = project_dir / "agents" / "linux" / "config.json"
    service_file = project_dir / "deployment" / "sysmon-agent.service"
    spec_file = rpm_dir / "sysmon-agent.spec"

    for f in [agent_sh, config_json, service_file, spec_file]:
        if not f.exists():
            print(f"Error: Required file not found: {f}")
            return

    # 1. Create source tarball
    tarball_name = f"{PKG_NAME}-{VERSION}"
    tarball_filename = f"{tarball_name}.tar.gz"
    tarball_path = rpm_dir / tarball_filename

    print(f">>> Creating source tarball: {tarball_filename}")

    with tarfile.open(str(tarball_path), "w:gz") as tar:
        # Add agent.sh
        tar.add(str(agent_sh), arcname=f"{tarball_name}/agent.sh")
        # Add config.json
        tar.add(str(config_json), arcname=f"{tarball_name}/config.json")
        # Add systemd service file
        tar.add(str(service_file), arcname=f"{tarball_name}/sysmon-agent.service")

    print(f"    Source tarball created: {tarball_path.absolute()}")
    print(f"    Size: {tarball_path.stat().st_size} bytes")

    # 2. Check if rpmbuild is available
    rpmbuild_available = shutil.which("rpmbuild") is not None

    if rpmbuild_available:
        print(">>> rpmbuild detected! Building RPM package...")

        # Setup rpmbuild directory structure
        rpmbuild_root = rpm_dir / "rpmbuild"
        for subdir in ["BUILD", "RPMS", "SOURCES", "SPECS", "SRPMS"]:
            (rpmbuild_root / subdir).mkdir(parents=True, exist_ok=True)

        # Copy source tarball to SOURCES
        shutil.copy2(str(tarball_path), str(rpmbuild_root / "SOURCES" / tarball_filename))

        # Copy spec file to SPECS
        shutil.copy2(str(spec_file), str(rpmbuild_root / "SPECS" / "sysmon-agent.spec"))

        # Run rpmbuild
        try:
            result = subprocess.run(
                [
                    "rpmbuild",
                    "-bb",
                    "--define", f"_topdir {rpmbuild_root.absolute()}",
                    str(rpmbuild_root / "SPECS" / "sysmon-agent.spec"),
                ],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                # Find the built RPM
                rpms_dir = rpmbuild_root / "RPMS" / "noarch"
                rpm_files = list(rpms_dir.glob("*.rpm"))
                if rpm_files:
                    # Copy RPM to the rpm/ directory
                    output_rpm = rpm_dir / rpm_files[0].name
                    shutil.copy2(str(rpm_files[0]), str(output_rpm))
                    print("=" * 60)
                    print("Success! RPM package created:")
                    print(f"   {output_rpm.absolute()}")
                    print(f"   Size: {output_rpm.stat().st_size} bytes")
                    print()
                    print("Install with:")
                    print(f"   sudo rpm -i {output_rpm.name}")
                    print("=" * 60)
                else:
                    print("Warning: rpmbuild completed but no RPM file found.")
                    print(result.stdout)
            else:
                print(f"Error: rpmbuild failed with exit code {result.returncode}")
                print(result.stderr)
        except Exception as e:
            print(f"Error running rpmbuild: {e}")

        # Cleanup rpmbuild tree
        shutil.rmtree(str(rpmbuild_root), ignore_errors=True)

    else:
        # rpmbuild not available — provide instructions
        print()
        print("=" * 60)
        print("rpmbuild is NOT available on this system.")
        print()
        print("Source tarball created successfully at:")
        print(f"   {tarball_path.absolute()}")
        print()
        print("To build the RPM on a Linux machine with rpmbuild:")
        print()
        print("  1. Install rpmbuild:")
        print("     sudo yum install rpm-build    # CentOS/RHEL")
        print("     sudo dnf install rpm-build    # Fedora")
        print()
        print("  2. Setup rpmbuild environment:")
        print("     mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}")
        print()
        print(f"  3. Copy files:")
        print(f"     cp {tarball_filename} ~/rpmbuild/SOURCES/")
        print(f"     cp sysmon-agent.spec ~/rpmbuild/SPECS/")
        print()
        print("  4. Build the RPM:")
        print("     rpmbuild -bb ~/rpmbuild/SPECS/sysmon-agent.spec")
        print()
        print("  5. Find your RPM at:")
        print("     ~/rpmbuild/RPMS/noarch/sysmon-agent-*.rpm")
        print("=" * 60)


if __name__ == "__main__":
    main()
