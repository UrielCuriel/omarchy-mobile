#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Make the stage 2 DTB for the wly kexec chain-loader.

Usage: stage2-dtb.py <built wly.dtb> <live.dtb> <out.dtb> [rscpd] [usb] [splash]

Adapted from device/dodge/chainload/stage2-dtb.py. ABL fills the `memory`
node at boot and kexec does not, so its reg is taken from the phone's live
FDT (/sys/firmware/fdt from a rooted capture, kept outside git). The board
DTS already carries the splash framebuffer and a USB 2.0-only peripheral
port, so dodge's fb, peripheral and hs options are not needed here.

rscpd   drop power-domains from the apps RSC (rsc@17a00000), so RPMh does
        not wait on PSCI's cpuidle genpd, which does not bind after kexec
        (dodge's finding on sm8750; sm8450's apps RSC has the same supplier)
usb     drop usb-role-switch and the dwc3 iommus entry: the role would
        come from pmic_glink, which the initramfs does not load, and dodge
        found the stream blocked under ARM_SMMU_DISABLE_BYPASS_BY_DEFAULT
        after kexec
splash  label ABL's splash reservation and give mdss an identity map of
        it, so arm-smmu keeps the scan-out working (mdss stays disabled;
        untried on this phone)
"""
import re
import subprocess
import sys


def dts(path):
    return subprocess.run(["dtc", "-q", "-I", "dtb", "-O", "dts", path],
                          capture_output=True, text=True, check=True).stdout


def node_span(s, header):
    i = s.index(header)
    depth = 0
    for k in range(i, len(s)):
        if s[k] == "{":
            depth += 1
        elif s[k] == "}":
            depth -= 1
            if depth == 0:
                return i, k
    raise ValueError(header)


def memory_span(s):
    """The live FDT uses `memory {` (ABL); the board DTB `memory@a0000000`."""
    try:
        return node_span(s, "\n\tmemory {")
    except ValueError:
        pass
    m = re.search(r"\n\tmemory@[0-9a-fA-F]+ \{", s)
    if not m:
        raise SystemExit("no memory node")
    return node_span(s, m.group(0))


def drop_prop(s, header, prop, indent):
    """Remove one property line, at the node's own depth, from a node."""
    i, j = node_span(s, header)
    m = re.search(r"\n" + "\t" * indent + re.escape(prop) + r"( = [^;]*)?;", s[i:j])
    if not m:
        raise SystemExit(f"expected {prop} in {header.strip()}")
    return s[:i + m.start()] + s[i + m.end():]


built, live, out, *opts = sys.argv[1:]
for o in opts:
    if o not in ("rscpd", "usb", "splash"):
        raise SystemExit(f"unknown option {o}")
ours, theirs = dts(built), dts(live)

i, j = memory_span(theirs)
live_reg = re.search(r"reg = <[^>]*>;", theirs[i:j]).group(0)
i, j = memory_span(ours)
block = ours[i:j]
ours = ours[:i] + block.replace(re.search(r"reg = <[^>]*>;", block).group(0), live_reg) + ours[j:]
if len(re.findall(r"\n\tmemory(@[0-9a-fA-F]+)? \{", ours)) != 1:
    raise SystemExit("expected exactly one top-level memory node")

if "rscpd" in opts:
    ours = drop_prop(ours, "\n\t\trsc@17a00000 {", "power-domains", 3)

if "usb" in opts:
    ours = drop_prop(ours, "\n\t\tusb@a600000 {", "usb-role-switch", 3)
    ours = drop_prop(ours, "\n\t\tusb@a600000 {", "iommus", 3)

if "splash" in opts:
    if "splash-region@b8000000 {" not in ours or 'compatible = "qcom,sm8450-mdss";' not in ours:
        raise SystemExit("expected the splash reservation and the mdss node")
    ours = ours.replace("display-subsystem@ae00000 {", "mdss: display-subsystem@ae00000 {", 1)
    ours = ours.replace(
        'compatible = "qcom,sm8450-mdss";',
        'compatible = "qcom,sm8450-mdss";\n\t\t\tmemory-region = <&splash_mem>;', 1)
    ours = ours.replace(
        "splash-region@b8000000 {",
        "splash_mem: splash-region@b8000000 {\n"
        "\t\t\tiommu-addresses = <&mdss 0x00 0xb8000000 0x00 0x2b00000>;", 1)

subprocess.run(["dtc", "-q", "-I", "dts", "-O", "dtb", "-o", out, "-"],
               input=ours, text=True, check=True)
